"""
Supabase File Manager with Streamlit
A secure file manager with login, password recovery, and full file/folder management.
Uses Supabase Storage (API key auth - no OAuth required).
"""

import streamlit as st
import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from supabase import create_client, Client
import tempfile
import uuid
from datetime import datetime

# ============================================
# CONFIGURATION
# ============================================

def load_config():
    """Load configuration - uses Streamlit secrets for cloud, falls back to secrets.json for local"""
    # Try Streamlit secrets first (for Streamlit Cloud)
    try:
        if hasattr(st, 'secrets') and len(st.secrets) > 0:
            return dict(st.secrets)
    except:
        pass
    
    # Fall back to local secrets.json
    config_path = os.path.join(os.path.dirname(__file__), "secrets.json")
    try:
        with open(config_path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        st.error("❌ No secrets configured! Please set up Streamlit secrets or create secrets.json")
        st.stop()
    except json.JSONDecodeError:
        st.error("❌ secrets.json is invalid JSON!")
        st.stop()

def get_supabase_client() -> Client:
    """Get Supabase client"""
    config = load_config()
    supabase_config = config.get("supabase", {})
    
    url = supabase_config.get("url", "")
    key = supabase_config.get("key", "")
    
    if not url or not key:
        st.error("❌ Supabase URL and Key are required")
        st.stop()
    
    return create_client(url, key)

def get_bucket_name():
    """Get the storage bucket name"""
    config = load_config()
    return config.get("supabase", {}).get("bucket", "files")

# ============================================
# SUPABASE FILE MANAGER
# ============================================

def list_files(folder_path=""):
    """List files and folders in a directory"""
    supabase = get_supabase_client()
    bucket = get_bucket_name()
    items = []
    
    try:
        # List files in the folder
        result = supabase.storage.from_(bucket).list(folder_path)
        
        for item in result:
            name = item.get('name', '')
            is_folder = item.get('id') is None  # Folders don't have id
            
            if is_folder:
                items.append({
                    'id': f"{folder_path}/{name}" if folder_path else name,
                    'name': name,
                    'type': 'folder',
                    'path': f"{folder_path}/{name}" if folder_path else name,
                    'size': 'N/A'
                })
            else:
                file_path = f"{folder_path}/{name}" if folder_path else name
                items.append({
                    'id': file_path,
                    'name': name,
                    'type': 'file',
                    'path': file_path,
                    'size': item.get('metadata', {}).get('size', 0) if item.get('metadata') else 0,
                    'created_at': item.get('created_at', '')
                })
    except Exception as e:
        if "not found" not in str(e).lower():
            st.error(f"Error listing files: {str(e)}")
    
    # Sort: folders first, then files
    items.sort(key=lambda x: (0 if x['type'] == 'folder' else 1, x['name'].lower()))
    return items

def upload_file(uploaded_file, folder_path=""):
    """Upload a file to Supabase Storage"""
    supabase = get_supabase_client()
    bucket = get_bucket_name()
    
    # Create file path
    file_path = f"{folder_path}/{uploaded_file.name}" if folder_path else uploaded_file.name
    
    # Read file content
    file_content = uploaded_file.getvalue()
    
    try:
        # Upload to Supabase
        result = supabase.storage.from_(bucket).upload(
            file_path,
            file_content,
            file_options={"content-type": uploaded_file.type or "application/octet-stream"}
        )
        
        return file_path
    except Exception as e:
        # If file exists, try to update it
        if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
            try:
                supabase.storage.from_(bucket).update(
                    file_path,
                    file_content,
                    file_options={"content-type": uploaded_file.type or "application/octet-stream"}
                )
                return file_path
            except Exception as e2:
                raise Exception(f"Upload failed: {str(e2)}")
        else:
            raise Exception(f"Upload failed: {str(e)}")

def get_file_url(file_path):
    """Get a public URL for a file"""
    supabase = get_supabase_client()
    bucket = get_bucket_name()
    
    try:
        # Get public URL
        result = supabase.storage.from_(bucket).get_public_url(file_path)
        return result
    except:
        # Create signed URL if public doesn't work
        try:
            result = supabase.storage.from_(bucket).create_signed_url(file_path, 3600)
            return result.get('signedURL', '')
        except:
            return ""

def download_file(file_path):
    """Download a file from Supabase Storage"""
    supabase = get_supabase_client()
    bucket = get_bucket_name()
    
    try:
        result = supabase.storage.from_(bucket).download(file_path)
        return result
    except Exception as e:
        raise Exception(f"Download failed: {str(e)}")

def delete_file(file_path):
    """Delete a file from Supabase Storage"""
    supabase = get_supabase_client()
    bucket = get_bucket_name()
    
    try:
        result = supabase.storage.from_(bucket).remove([file_path])
        return True
    except Exception as e:
        st.error(f"Delete failed: {str(e)}")
        return False

def rename_file(old_path, new_name):
    """Rename a file in Supabase Storage"""
    supabase = get_supabase_client()
    bucket = get_bucket_name()
    
    # Get folder path
    parts = old_path.rsplit('/', 1)
    folder = parts[0] if len(parts) > 1 else ""
    new_path = f"{folder}/{new_name}" if folder else new_name
    
    try:
        # Download the file
        content = supabase.storage.from_(bucket).download(old_path)
        
        # Upload with new name
        supabase.storage.from_(bucket).upload(new_path, content)
        
        # Delete old file
        supabase.storage.from_(bucket).remove([old_path])
        
        return True
    except Exception as e:
        st.error(f"Rename failed: {str(e)}")
        return False

def create_folder(folder_name, parent_folder=""):
    """Create a new folder in Supabase Storage"""
    supabase = get_supabase_client()
    bucket = get_bucket_name()
    
    folder_path = f"{parent_folder}/{folder_name}" if parent_folder else folder_name
    
    try:
        # Create an empty .keep file to create the folder
        placeholder_path = f"{folder_path}/.keep"
        supabase.storage.from_(bucket).upload(
            placeholder_path,
            b"",
            file_options={"content-type": "text/plain"}
        )
        return True
    except Exception as e:
        st.error(f"Failed to create folder: {str(e)}")
        return False

def delete_folder(folder_path):
    """Delete a folder from Supabase Storage"""
    supabase = get_supabase_client()
    bucket = get_bucket_name()
    
    try:
        # List all files in folder
        files = supabase.storage.from_(bucket).list(folder_path)
        
        # Delete all files
        for file in files:
            file_path = f"{folder_path}/{file['name']}"
            supabase.storage.from_(bucket).remove([file_path])
        
        return True
    except Exception as e:
        st.error(f"Failed to delete folder: {str(e)}")
        return False

def move_to_trash(file_path):
    """Move a file to the recycle bin folder"""
    supabase = get_supabase_client()
    bucket = get_bucket_name()
    
    try:
        # Download the file first
        content = supabase.storage.from_(bucket).download(file_path)
        
        # Create trash path
        filename = file_path.split('/')[-1]
        trash_path = f".trash/{filename}"
        
        # Upload to trash
        supabase.storage.from_(bucket).upload(
            trash_path,
            content,
            file_options={"content-type": "application/octet-stream", "upsert": "true"}
        )
        
        # Delete original
        supabase.storage.from_(bucket).remove([file_path])
        
        return True
    except Exception as e:
        st.error(f"Failed to move to trash: {str(e)}")
        return False

def list_trash():
    """List files in the recycle bin"""
    supabase = get_supabase_client()
    bucket = get_bucket_name()
    items = []
    
    try:
        result = supabase.storage.from_(bucket).list(".trash")
        
        for item in result:
            name = item.get('name', '')
            if name.startswith('.'):
                continue
            
            file_path = f".trash/{name}"
            items.append({
                'id': file_path,
                'name': name,
                'path': file_path,
                'size': item.get('metadata', {}).get('size', 0) if item.get('metadata') else 0
            })
    except:
        pass  # Trash folder might not exist
    
    return items

def restore_from_trash(trash_path):
    """Restore a file from recycle bin"""
    supabase = get_supabase_client()
    bucket = get_bucket_name()
    
    try:
        # Download from trash
        content = supabase.storage.from_(bucket).download(trash_path)
        
        # Get original filename
        filename = trash_path.split('/')[-1]
        
        # Upload to root
        supabase.storage.from_(bucket).upload(
            filename,
            content,
            file_options={"content-type": "application/octet-stream", "upsert": "true"}
        )
        
        # Delete from trash
        supabase.storage.from_(bucket).remove([trash_path])
        
        return True
    except Exception as e:
        st.error(f"Failed to restore: {str(e)}")
        return False

def empty_trash():
    """Permanently delete all files in recycle bin"""
    supabase = get_supabase_client()
    bucket = get_bucket_name()
    
    try:
        files = supabase.storage.from_(bucket).list(".trash")
        
        for file in files:
            file_path = f".trash/{file['name']}"
            supabase.storage.from_(bucket).remove([file_path])
        
        return True
    except Exception as e:
        st.error(f"Failed to empty trash: {str(e)}")
        return False

def permanent_delete_from_trash(trash_path):
    """Permanently delete a file from recycle bin"""
    supabase = get_supabase_client()
    bucket = get_bucket_name()
    
    try:
        supabase.storage.from_(bucket).remove([trash_path])
        return True
    except Exception as e:
        st.error(f"Failed to delete: {str(e)}")
        return False

def change_password(new_password):
    """Change the login password (only works in local environment)"""
    config_path = os.path.join(os.path.dirname(__file__), "secrets.json")
    
    # Check if we're on Streamlit Cloud (secrets.json won't exist)
    if not os.path.exists(config_path):
        st.warning("⚠️ Password change is not available on cloud. Update secrets in Streamlit Cloud dashboard.")
        return False
    
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
        
        config["login_password"] = new_password
        
        with open(config_path, "w") as f:
            json.dump(config, f, indent=4)
        
        return True
    except Exception as e:
        st.error(f"Failed to change password: {str(e)}")
        return False

# ============================================
# EMAIL MANAGER
# ============================================

def send_password_email():
    """Send password recovery email"""
    config = load_config()
    
    smtp_config = config["smtp"]
    to_email = config["recovery_email"]
    password = config["login_password"]
    
    msg = MIMEMultipart()
    msg['From'] = smtp_config["username"]
    msg['To'] = to_email
    msg['Subject'] = "🔐 File Manager - Password Recovery"
    
    body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; padding: 20px; background-color: #f5f5f5;">
        <div style="max-width: 500px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
            <h2 style="color: #1a73e8;">🔐 Password Recovery</h2>
            <p>You requested your password for the File Manager.</p>
            <div style="background-color: #e8f0fe; padding: 15px; border-radius: 5px; margin: 20px 0;">
                <strong>Your Password:</strong> <code style="font-size: 18px; color: #1a73e8;">{password}</code>
            </div>
            <p style="color: #666; font-size: 12px;">If you didn't request this, please ignore this email.</p>
        </div>
    </body>
    </html>
    """
    
    msg.attach(MIMEText(body, 'html'))
    
    try:
        server = smtplib.SMTP(smtp_config["host"], smtp_config["port"])
        server.starttls()
        server.login(smtp_config["username"], smtp_config["password"])
        server.send_message(msg)
        server.quit()
        return True, "Password sent to your email!"
    except Exception as e:
        return False, f"Failed to send email: {str(e)}"

# ============================================
# AUTHENTICATION
# ============================================

def check_password(password):
    """Verify password against stored password"""
    config = load_config()
    return password == config["login_password"]

def is_logged_in():
    """Check if user is logged in"""
    return st.session_state.get("logged_in", False)

def login():
    """Set logged in state"""
    st.session_state.logged_in = True

def logout():
    """Clear login state"""
    st.session_state.logged_in = False
    st.session_state.current_folder = ""
    st.session_state.folder_stack = []

# ============================================
# UI COMPONENTS
# ============================================

def render_login_page():
    """Render the login page"""
    st.markdown("""
    <style>
        .stButton > button {
            width: 100%;
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            border: none;
            padding: 12px;
            border-radius: 10px;
            color: white;
            font-weight: bold;
            transition: all 0.3s ease;
        }
        .stButton > button:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(102, 126, 234, 0.4);
        }
    </style>
    """, unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.markdown("### 🔐 File Manager Login")
        st.markdown("---")
        
        password = st.text_input("Password", type="password", placeholder="Enter your password")
        
        col_login, col_forgot = st.columns(2)
        
        with col_login:
            if st.button("🔓 Login", use_container_width=True):
                if check_password(password):
                    login()
                    st.success("✅ Login successful!")
                    st.rerun()
                else:
                    st.error("❌ Invalid password!")
        
        with col_forgot:
            if st.button("🔑 Forgot Password", use_container_width=True):
                with st.spinner("Sending email..."):
                    success, message = send_password_email()
                    if success:
                        st.success(message)
                    else:
                        st.error(message)

def render_file_manager():
    """Render the main file manager page"""
    # Initialize session state
    if "current_folder" not in st.session_state:
        st.session_state.current_folder = ""
    if "folder_stack" not in st.session_state:
        st.session_state.folder_stack = []
    if "upload_success" not in st.session_state:
        st.session_state.upload_success = None
    if "view_mode" not in st.session_state:
        st.session_state.view_mode = "files"  # "files" or "trash"
    
    # Sidebar
    with st.sidebar:
        st.markdown("## ⚙️ Settings")
        st.markdown("---")
        
        # Change Password
        st.markdown("### 🔐 Change Password")
        new_password = st.text_input("New Password", type="password", key="new_pass")
        confirm_password = st.text_input("Confirm Password", type="password", key="confirm_pass")
        
        if st.button("🔄 Update Password", use_container_width=True):
            if new_password and confirm_password:
                if new_password == confirm_password:
                    if change_password(new_password):
                        st.success("✅ Password changed!")
                else:
                    st.error("❌ Passwords don't match!")
            else:
                st.warning("Enter both fields")
        
        st.markdown("---")
        
        # Recycle Bin
        st.markdown("### 🗑️ Recycle Bin")
        
        trash_items = list_trash()
        st.caption(f"{len(trash_items)} items in trash")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("📂 View Trash", use_container_width=True):
                st.session_state.view_mode = "trash"
                st.rerun()
        with col2:
            if st.button("🗑️ Empty", use_container_width=True):
                if empty_trash():
                    st.success("✅ Trash emptied!")
                    st.rerun()
        
        if st.session_state.view_mode == "trash":
            if st.button("📁 Back to Files", use_container_width=True):
                st.session_state.view_mode = "files"
                st.rerun()
        
        st.markdown("---")
        
        # Logout
        if st.button("🚪 Logout", use_container_width=True):
            logout()
            st.rerun()
    
    # Main content
    if st.session_state.view_mode == "trash":
        render_trash_view()
    else:
        render_files_view()

def render_files_view():
    """Render the main files view"""
    # Show upload success message if any
    if st.session_state.upload_success:
        st.success(st.session_state.upload_success)
        st.session_state.upload_success = None
    
    # Header
    st.markdown("## 📁 File Manager")
    st.markdown("---")
    
    # Breadcrumb navigation
    render_breadcrumb()
    
    # Action buttons row
    st.markdown("### 📤 Actions")
    col1, col2 = st.columns(2)
    
    with col1:
        uploaded_file = st.file_uploader(
            "Upload a file",
            key="file_uploader",
            help="Select a file to upload"
        )
        if uploaded_file is not None:
            if st.button("📤 Upload File", key="upload_btn", use_container_width=True):
                with st.spinner(f"Uploading {uploaded_file.name}..."):
                    try:
                        file_id = upload_file(uploaded_file, st.session_state.current_folder)
                        st.session_state.upload_success = f"✅ Uploaded: {uploaded_file.name}"
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌ Upload failed: {str(e)}")
    
    with col2:
        st.markdown("**Create New Folder**")
        new_folder_name = st.text_input("Folder name", key="new_folder_name", label_visibility="collapsed", placeholder="Enter folder name")
        if st.button("📁 Create Folder", key="create_folder_btn", use_container_width=True):
            if new_folder_name:
                if create_folder(new_folder_name, st.session_state.current_folder):
                    st.success(f"✅ Created: {new_folder_name}")
                    st.rerun()
            else:
                st.warning("Please enter a folder name")
    
    st.markdown("---")
    
    # List files and folders
    render_file_list()

def render_trash_view():
    """Render the recycle bin view"""
    st.markdown("## 🗑️ Recycle Bin")
    st.markdown("---")
    
    trash_items = list_trash()
    
    if not trash_items:
        st.info("🗑️ Recycle bin is empty")
        return
    
    for item in trash_items:
        with st.container():
            cols = st.columns([0.5, 4, 1, 1])
            
            with cols[0]:
                st.markdown(f"### {get_file_icon(item['name'])}")
            
            with cols[1]:
                st.markdown(f"**{item['name']}**")
                size = format_file_size(item.get('size', 0))
                st.caption(f"Size: {size}")
            
            with cols[2]:
                if st.button("♻️ Restore", key=f"restore_{item['id']}"):
                    if restore_from_trash(item['path']):
                        st.success(f"✅ Restored: {item['name']}")
                        st.rerun()
            
            with cols[3]:
                if st.button("🗑️ Delete", key=f"perm_del_{item['id']}"):
                    if permanent_delete_from_trash(item['path']):
                        st.success("✅ Permanently deleted")
                        st.rerun()
            
            st.markdown("---")

def render_breadcrumb():
    """Render folder navigation breadcrumb"""
    breadcrumb_items = ["🏠 Home"]
    
    for folder_name in st.session_state.folder_stack:
        breadcrumb_items.append(folder_name)
    
    breadcrumb_text = " → ".join(breadcrumb_items)
    
    cols = st.columns([6, 2])
    with cols[0]:
        st.markdown(f"**📍 {breadcrumb_text}**")
    
    with cols[1]:
        if st.session_state.folder_stack:
            if st.button("⬆️ Go Back", use_container_width=True):
                st.session_state.folder_stack.pop()
                if st.session_state.folder_stack:
                    st.session_state.current_folder = "/".join(st.session_state.folder_stack)
                else:
                    st.session_state.current_folder = ""
                st.rerun()

def render_file_list():
    """Render the file and folder list"""
    try:
        items = list_files(st.session_state.current_folder)
    except Exception as e:
        st.error(f"❌ Failed to load files: {str(e)}")
        return
    
    if not items:
        st.info("📭 This folder is empty")
        return
    
    # Filter out .keep files
    items = [item for item in items if not item['name'].startswith('.')]
    
    if not items:
        st.info("📭 This folder is empty")
        return
    
    # Display items
    for item in items:
        render_file_item(item)

def render_file_item(item):
    """Render a single file or folder item"""
    is_folder = item['type'] == 'folder'
    icon = "📁" if is_folder else get_file_icon(item['name'])
    
    with st.container():
        cols = st.columns([0.5, 4, 1, 1, 1])
        
        with cols[0]:
            st.markdown(f"### {icon}")
        
        with cols[1]:
            if is_folder:
                if st.button(f"**{item['name']}**", key=f"folder_{item['id']}", use_container_width=True):
                    st.session_state.folder_stack.append(item['name'])
                    st.session_state.current_folder = item['path']
                    st.rerun()
            else:
                st.markdown(f"**{item['name']}**")
                size = format_file_size(item.get('size', 0))
                st.caption(f"Size: {size}")
        
        # View/Download button (files only)
        with cols[2]:
            if not is_folder:
                url = get_file_url(item['path'])
                if url:
                    st.markdown(f"[👁️ View]({url})")
        
        # Rename button
        with cols[3]:
            with st.popover("✏️", help="Rename"):
                current_name = item['name']
                new_name = st.text_input("New name", value=current_name, key=f"rename_input_{item['id']}")
                if st.button("Rename", key=f"rename_btn_{item['id']}"):
                    if new_name and new_name != current_name:
                        if is_folder:
                            st.warning("Folder renaming is not supported")
                        else:
                            if rename_file(item['path'], new_name):
                                st.success("✅ Renamed!")
                                st.rerun()
        
        # Delete button (moves to trash for files)
        with cols[4]:
            with st.popover("🗑️", help="Delete"):
                st.warning(f"Delete '{item['name']}'?")
                if st.button("🗑️ Move to Trash", key=f"delete_btn_{item['id']}"):
                    if is_folder:
                        if delete_folder(item['path']):
                            st.success("✅ Folder deleted!")
                            st.rerun()
                    else:
                        if move_to_trash(item['path']):
                            st.success("✅ Moved to trash!")
                            st.rerun()
        
        st.markdown("---")

def get_file_icon(filename):
    """Get appropriate icon for file type"""
    ext = filename.lower().split('.')[-1] if '.' in filename else ''
    
    icons = {
        'pdf': '📄',
        'doc': '📝', 'docx': '📝',
        'xls': '📊', 'xlsx': '📊',
        'ppt': '📽️', 'pptx': '📽️',
        'jpg': '🖼️', 'jpeg': '🖼️', 'png': '🖼️', 'gif': '🖼️', 'webp': '🖼️',
        'mp4': '🎬', 'avi': '🎬', 'mov': '🎬', 'mkv': '🎬',
        'mp3': '🎵', 'wav': '🎵', 'flac': '🎵',
        'zip': '📦', 'rar': '📦', '7z': '📦',
        'txt': '📃',
        'py': '🐍',
        'js': '💛', 'ts': '💙',
        'html': '🌐', 'css': '🎨',
        'json': '📋',
    }
    
    return icons.get(ext, '📄')

def format_file_size(size_bytes):
    """Format file size in human readable format"""
    if size_bytes == 0 or size_bytes == 'N/A':
        return 'N/A'
    try:
        size_bytes = int(size_bytes)
    except:
        return 'N/A'
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"

# ============================================
# MAIN APP
# ============================================

def main():
    """Main application entry point"""
    st.set_page_config(
        page_title="📁 Supabase File Manager",
        page_icon="📁",
        layout="wide",
        initial_sidebar_state="collapsed"
    )
    
    # Custom CSS for modern look
    st.markdown("""
    <style>
        /* Dark theme styling */
        .stApp {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
        }
        
        /* Button styling */
        .stButton > button {
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            padding: 8px 16px;
            transition: all 0.3s ease;
        }
        
        .stButton > button:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(102, 126, 234, 0.4);
        }
        
        /* Hide Streamlit branding */
        #MainMenu {visibility: hidden;}
        footer {visibility: hidden;}
        header {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)
    
    # Route to login or file manager
    if is_logged_in():
        render_file_manager()
    else:
        render_login_page()

if __name__ == "__main__":
    main()
