import streamlit as st
import cloudinary
import cloudinary.uploader
import cloudinary.api
import cloudinary.utils
import json
import requests
import io
import smtplib
import ssl
import os
from email.message import EmailMessage

# --- Configuration (Bootstrap) ---
st.set_page_config(page_title="Cloudinary File Manager", layout="wide")

# We still need keys to access Cloudinary to get the config!
# So secrets.toml is still needed for KEYS. But 'password' will be dynamic.
try:
    cloudinary.config(
        cloud_name=st.secrets["cloudinary"]["cloud_name"],
        api_key=st.secrets["cloudinary"]["api_key"],
        api_secret=st.secrets["cloudinary"]["api_secret"]
    )
except Exception as e:
    st.error(f"Error loading secrets: {e}")
    st.stop()

# --- Cloud persistency helper ---
CONFIG_FILE_ID = "file_manager_config.json"

def get_cloud_config():
    """Downloads config from Cloudinary. Returns dict or None if not found."""
    try:
        res = cloudinary.api.resource(CONFIG_FILE_ID, resource_type="raw")
        url = res.get("secure_url")
        if url:
            resp = requests.get(url)
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        return None
    return None

def save_cloud_config(config_data):
    """Uploads config dict to Cloudinary as config.json."""
    try:
        # Upload as raw
        cloudinary.uploader.upload(
            io.BytesIO(json.dumps(config_data).encode('utf-8')),
            public_id=CONFIG_FILE_ID,
            resource_type="raw",
            overwrite=True
        )
        return True
    except Exception as e:
        st.error(f"Failed to save cloud config: {e}")
        return False

def send_password_email(current_password):
    try:
        sender_email = st.secrets["email"]["sender_email"]
        password = st.secrets["email"]["sender_password"]
        smtp_server = st.secrets["email"]["smtp_server"]
        smtp_port = st.secrets["email"]["smtp_port"]
        receiver_email = "m24srivastava@gmail.com"
        
        if "your_" in sender_email or "your_" in password:
            return False, "Please configure email secrets in .streamlit/secrets.toml"

        msg = EmailMessage()
        msg.set_content(f"Your current password is: {current_password}")
        msg['Subject'] = "Your Password Recovery"
        msg['From'] = sender_email
        msg['To'] = receiver_email
        
        context = ssl.create_default_context()
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls(context=context)
            server.login(sender_email, password)
            server.send_message(msg)
        return True, "Email sent successfully!"
    except Exception as e:
        return False, str(e)

# --- Initialization ---
# 1. Load Config
if "app_config" not in st.session_state:
    cloud_conf = get_cloud_config()
    if cloud_conf:
        st.session_state.app_config = cloud_conf
    else:
        # Default fallback
        st.session_state.app_config = {
            "password": st.secrets["general"].get("password", "admin")
        }
        # Attempt to create it for the first time
        save_cloud_config(st.session_state.app_config)

# 2. Auth State
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if "current_path" not in st.session_state:
    st.session_state.current_path = "" 

# --- Authentication ---
def check_password():
    # Check against the loaded config
    if st.session_state.password_input == st.session_state.app_config.get("password"):
        st.session_state.authenticated = True
        del st.session_state.password_input
    else:
        st.error("Incorrect password")

if not st.session_state.authenticated:
    st.title("Login")
    st.text_input("Enter Password", type="password", key="password_input", on_change=check_password)
    
    if st.button("Forgot Password?"):
        current_p = st.session_state.app_config.get("password")
        if current_p:
            with st.spinner("Sending email..."):
                success, msg = send_password_email(current_p)
                if success:
                    st.success(msg)
                else:
                    st.error(f"Failed: {msg}")
        else:
             st.error("Config not loaded.")
    
    st.stop()
    
# --- Navigation Helpers ---
def navigate_to(folder_name):
    if st.session_state.current_path:
        st.session_state.current_path = f"{st.session_state.current_path}/{folder_name}"
    else:
        st.session_state.current_path = folder_name

def navigate_up():
    if "/" in st.session_state.current_path:
        st.session_state.current_path = st.session_state.current_path.rsplit("/", 1)[0]
    else:
        st.session_state.current_path = ""

def navigate_home():
    st.session_state.current_path = ""

# --- Rename Helpers ---
def rename_item(public_id, new_name, is_folder=False):
    try:
        if is_folder:
            # Folder rename: 
            # Current: folder/path/old_name (usually we just have the path)
            # We want to rename 'folder/path/old_name' --> 'folder/path/new_name'
            
            # Note: arguments are (from_path, to_path)
            # public_id here acts as the full from_path for the folder
            
            parent = os.path.dirname(public_id)
            if parent:
                to_path = f"{parent}/{new_name}"
            else:
                to_path = new_name
            
            # API call
            # Using v2 api, hope rename_folder is supported in installed ver.
            # If not, we might need a workaround or catch AttributeError
            cloudinary.api.rename_folder(public_id, to_path)
            return True, to_path
        else:
            # File rename:
            # public_id: folder/file
            # target: folder/new_name
            
            # Construct target path
            # We need to preserve the folder path!
            
            folder = os.path.dirname(public_id)
            if folder:
                target_public_id = f"{folder}/{new_name}"
            else:
                target_public_id = new_name
            
            # Cloudinary rename doesn't need extension usually if keeping same format?
            # Actually public_id does not have extension usually.
            # But 'new_name' input might not have it.
            # Let's assume user inputs simple name.
            
            cloudinary.uploader.rename(public_id, target_public_id, overwrite=True)
            return True, target_public_id
            
    except Exception as e:
        return False, str(e)


def get_contents(path, mode="Active"):
    folders = []
    files = []
    
    # helper to fetch by type
    def fetch_by_type(r_type):
        try:
            prefix = path + "/" if path else ""
            params = {
                "resource_type": r_type, 
                "max_results": 50, 
                "tags": True, 
                "context": True,
                "type": "upload"
            }
            if prefix:
                params["prefix"] = prefix
            
            resp = cloudinary.api.resources(**params)
            return resp.get("resources", [])
        except Exception:
            return []

    try:
        if mode == "Active Files":
            try:
                sub_resp = cloudinary.api.subfolders(path) if path else cloudinary.api.root_folders()
                folders = sub_resp.get("folders", [])
            except Exception:
                pass

        # Fetch all types
        raw_files = []
        raw_files.extend(fetch_by_type("image"))
        raw_files.extend(fetch_by_type("video"))
        raw_files.extend(fetch_by_type("raw"))
        
        prefix = path + "/" if path else ""
        
        for res in raw_files:
            tags = res.get("tags", [])
            is_deleted = "status:deleted" in tags
            public_id = res.get("public_id")
            
            # Hide the Config File
            if public_id == CONFIG_FILE_ID:
                continue
            
            # Depth Check
            if prefix and public_id.startswith(prefix):
                rel_path = public_id[len(prefix):]
            elif prefix:
                 # Should not happen if API filters correctly, but safety check
                 continue
            else:
                rel_path = public_id

            if "/" in rel_path:
                continue

            if mode == "Recycle Bin" and is_deleted:
                files.append(res)
            elif mode == "Active Files" and not is_deleted:
                files.append(res)
                
    except Exception as e:
        st.error(f"Error fetching contents: {e}")
        
    # Sort files by created_at maybe? For now just return list
    return folders, files


# --- Main UI ---
st.title("📂 Cloudinary File Manager")

with st.sidebar:
    st.header("Actions")
    mode = st.radio("View Mode", ["Active Files", "Recycle Bin"])
    st.divider()
    with st.expander("Change Password"):
        current_pass = st.text_input("Current Password", type="password")
        new_pass = st.text_input("New Password", type="password")
        confirm_pass = st.text_input("Confirm New Password", type="password")
        if st.button("Update Password"):
            if current_pass != st.session_state.app_config.get("password"):
                st.error("Current password incorrect.")
            elif new_pass != confirm_pass:
                st.error("New passwords do not match.")
            elif not new_pass:
                st.error("Password cannot be empty.")
            else:
                # Update config state
                new_config = st.session_state.app_config.copy()
                new_config["password"] = new_pass
                
                # Save to Cloud
                if save_cloud_config(new_config):
                    st.session_state.app_config = new_config
                    st.success("Password updated & saved to Cloud!")
                else:
                    st.error("Failed to save to cloud.")
                    
    st.divider()
    if st.button("Logout"):
        st.session_state.authenticated = False
        st.rerun()

# --- Active View Navigation ---
if mode == "Active Files":
    c1, c2 = st.columns([3, 1])
    with c1:
        path_str = "Home" 
        if st.session_state.current_path:
            path_str += " / " + st.session_state.current_path.replace("/", " / ")
        st.markdown(f"**📍 Path:** `{path_str}`")
        if st.session_state.current_path:
            if st.button("⬅️ Up One Level"):
                navigate_up()
                st.rerun()
    with c2:
        if st.button("🏠 Home"):
            navigate_home()
            st.rerun()

    with st.expander("Upload & Organize", expanded=True):
        uc1, uc2 = st.columns(2)
        with uc1:
            st.subheader("Upload File")
            uploaded_file = st.file_uploader("Choose file", key=f"uploader_{st.session_state.current_path}")
            if uploaded_file and st.button("Upload"):
                with st.spinner("Uploading..."):
                    try:
                        folder_path = st.session_state.current_path if st.session_state.current_path else None
                        
                        # Determine resource type dynamically if possible, or auto
                        # For raw files (txt, unknown), use 'raw' or 'auto'
                        cloudinary.uploader.upload(uploaded_file, folder=folder_path, resource_type="auto", tags=["status:active"])
                        st.success("Uploaded!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Upload error: {e}")
        with uc2:
            st.subheader("Create Folder")
            new_folder = st.text_input("Folder Name")
            if st.button("Create"):
                if new_folder:
                    try:
                        full_new_path = f"{st.session_state.current_path}/{new_folder}" if st.session_state.current_path else new_folder
                        cloudinary.api.create_folder(full_new_path)
                        st.success(f"Created {new_folder}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error creating folder: {e}")

else:
    st.subheader("🗑️ Recycle Bin (Global)")
    st.info("Recycle bin shows deleted files from ALL folders.")

st.divider()
if st.button("Refresh View"):
    st.rerun()

# --- Content ---
folders, files = get_contents(st.session_state.current_path, mode)

if mode == "Active Files" and folders:
    st.markdown("### 📁 Folders")
    for i, folder in enumerate(folders):
        folder_name = folder.get("name")
        folder_path = folder.get("path")
        
        # Container for each folder
        with st.container(border=True):
            col_a, col_b, col_c = st.columns([3, 0.5, 0.5])
            with col_a:
                if st.button(f"📁 {folder_name}", key=f"nav_{folder_path}", use_container_width=True):
                    navigate_to(folder_name)
                    st.rerun()
            with col_b:
                # Rename Folder Popover
                with st.popover("✏️", help="Rename Folder"):
                    new_f_name = st.text_input("Rename to:", value=folder_name, key=f"ren_f_input_{folder_path}")
                    if st.button("Save", key=f"ren_f_btn_{folder_path}"):
                        success, res = rename_item(folder_path, new_f_name, is_folder=True)
                        if success:
                            st.success("Renamed!")
                            st.rerun()
                        else:
                            st.error(f"Error: {res}")
            with col_c:
                # Delete Folder Button
                if st.button("❌", key=f"del_folder_{folder_path}", help="Delete Folder (Must be empty)"):
                    try:
                        cloudinary.api.delete_folder(folder_path)
                        st.success(f"Deleted {folder_name}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed: {e}")

if files:
    st.markdown("### 📄 Files")
    cols = st.columns(3)
    for idx, file in enumerate(files):
        with cols[idx % 3]:
            public_id = file.get("public_id")
            url = file.get("secure_url")
            resource_type = file.get("resource_type", "image")
            format_ext = file.get("format", "")
            
            # Preview Logic
            if resource_type == "image":
                st.image(url, use_container_width=True)
            elif resource_type == "video":
                st.video(url)
            elif resource_type == "raw" or format_ext == "pdf":
                 # For raw files, show a generic icon or updated preview if supported
                 st.markdown(f"📄 **{format_ext.upper()} File**")
                 
            st.caption(f"{public_id}")
            
            # Actions
            c1, c2, c3 = st.columns(3)
            with c1:
                # Generic 'View' should open in new tab for best compatibility with PDFs/Videos/Raw
                st.link_button("View", url)
            with c2:
                # Robust Download Link
                dl_url, options = cloudinary.utils.cloudinary_url(
                    public_id, 
                    flags="attachment",
                    resource_type=resource_type
                )
                st.link_button("⬇️", dl_url)
            with c3:
                # Rename File Popover
                with st.popover("✏️"):
                    # Extract just the filename part for default value
                    current_name = public_id.split("/")[-1]
                    new_name = st.text_input("New Name", value=current_name, key=f"ren_file_{public_id}")
                    if st.button("Save", key=f"ren_file_btn_{public_id}"):
                        success, res = rename_item(public_id, new_name, is_folder=False)
                        if success:
                            st.success("Renamed!")
                            st.rerun()
                        else:
                            st.error(f"Error: {res}")
            
            # Separate Row for Delete to avoid crowding
            if mode == "Active Files":
                if st.button("🗑️ Move to Bin", key=f"del_{public_id}", use_container_width=True):
                    cloudinary.uploader.add_tag("status:deleted", [public_id])
                    st.toast("Moved to Bin")
                    st.rerun()
            else:
                 c_r, c_d = st.columns(2)
                 with c_r:
                     if st.button("♻️ Restore", key=f"rest_{public_id}"):
                         cloudinary.uploader.remove_tag("status:deleted", [public_id])
                         st.toast("Restored")
                         st.rerun()
                 with c_d:
                     if st.button("❌ Perm", key=f"perm_{public_id}", type="primary"):
                         cloudinary.uploader.destroy(public_id)
                         st.rerun()

elif not folders:
    st.info("Empty folder.")
