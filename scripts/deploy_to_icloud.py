#!/usr/bin/env python3
import os
import sys
import time
import subprocess
import tempfile
from dotenv import load_dotenv
from pyicloud import PyiCloudService
from pyicloud.services.drive import DriveNode
from pyicloud.exceptions import PyiCloudFailedLoginException, PyiCloudAPIResponseException

def retry_api_call(func, *args, retries=3, delay=2, **kwargs):
    """Retry an API call with exponential backoff."""
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except PyiCloudAPIResponseException as e:
            if e.code == 503 or e.code == 500:
                if i == retries - 1:
                    raise
                sleep_time = delay * (2 ** i)
                print(f"API Error {e.code}. Retrying in {sleep_time}s...")
                time.sleep(sleep_time)
            else:
                raise
        except Exception as e:
            # Handle other potential transient errors if needed, 
            # but for now let's focus on API response exceptions.
            # We might want to retry on simple connection errors too.
            if i == retries - 1:
                raise
            print(f"An error occurred: {e}. Retrying...")
            time.sleep(delay)

# Configuration
load_dotenv()
ICLOUD_FOLDER_NAME = "CooklangApp"
STATE_FILE_NAME = "last_deployed_commit.txt"
# Default whitelist if not in env
DEFAULT_FOLDERS = "bread,config,desserts,entrees,salad,sides,soup"
FOLDERS_TO_SYNC = [f.strip() for f in os.environ.get('FOLDERS_TO_SYNC', DEFAULT_FOLDERS).split(',')]

def get_current_commit_hash():
    """Get the current HEAD commit hash."""
    return subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode('utf-8').strip()

def get_last_deployed_commit(app_folder):
    """Retrieve the last deployed commit hash from iCloud."""
    try:
        # pyicloud doesn't support reading file content directly easily without download
        # We need to download it to a temp file and read it
        file_node = app_folder[STATE_FILE_NAME]
        with tempfile.NamedTemporaryFile(mode='wb', delete=False) as temp:
            temp.write(file_node.open().content)
            temp_path = temp.name
        
        try:
            with open(temp_path, 'r') as f:
                commit_hash = f.read().strip()
            return commit_hash
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    except KeyError:
        return None
    except Exception as e:
        print(f"Warning: Could not read state file: {e}")
        return None

def update_last_deployed_commit(app_folder, commit_hash):
    """Update the state file in iCloud."""
    print(f"Updating state file to commit: {commit_hash}")
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as temp:
        temp.write(commit_hash)
        temp_path = temp.name
    
    final_path = os.path.join(os.path.dirname(temp_path), STATE_FILE_NAME)
    
    try:
        # Delete existing if present to ensure clean upload (though upload usually overwrites)
        try:
            app_folder[STATE_FILE_NAME].delete()
        except KeyError:
            pass
            
        # Rename the temp file locally first to ensure correct filename in iCloud
        if os.path.exists(final_path):
            os.remove(final_path)
        os.rename(temp_path, final_path)
        
        with open(final_path, 'rb') as f:
             retry_api_call(app_folder.upload, f)
             
    except Exception as e:
        print(f"Error updating state file: {e}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        if os.path.exists(final_path):
            os.remove(final_path)

def get_git_changes(last_hash, current_hash):
    """Get list of changed files between commits."""
    # --name-status returns: Status PATH
    # M modified, A added, D deleted
    output = subprocess.check_output(
        ['git', 'diff', '--name-status', last_hash, current_hash]
    ).decode('utf-8').strip()
    
    changes = []
    if not output:
        return changes
        
    for line in output.split('\n'):
        parts = line.split('\t')
        if len(parts) >= 2:
            status = parts[0][0] # First char of status (M, A, D, R, etc)
            path = parts[1]
            changes.append((status, path))
    return changes

def delete_file(icloud_folder, relative_path):
    """Delete a file from iCloud."""
    try:
        # Navigate to the file
        path_parts = relative_path.split(os.sep)
        node = icloud_folder
        for part in path_parts:
            node = node[part]
        
        print(f"Deleting '{relative_path}'...")
        node.delete()
    except KeyError:
        print(f"File '{relative_path}' not found in iCloud. Skipping delete.")
    except Exception as e:
        print(f"Error deleting '{relative_path}': {e}")


def get_icloud_service():
    """Authenticate with iCloud."""
    print("Authenticating with iCloud...")
    try:
        # PyiCloudService will look for credentials in the keyring or prompt the user
        api = PyiCloudService(os.environ.get('ICLOUD_USERNAME'))
    except PyiCloudFailedLoginException as e:
        print(f"Login failed: {e}")
        sys.exit(1)

    if api.requires_2fa:
        print("Two-factor authentication required.")
        code = input("Enter the code you received of one of your approved devices: ")
        result = api.validate_2fa_code(code)
        print("Code validation result: %s" % result)

        if not result:
            print("Failed to verify security code")
            sys.exit(1)

        if not api.is_trusted_session:
            print("Session is not trusted. Requesting trust...")
            result = api.trust_session()
            print("Session trust result %s" % result)

            if not result:
                print("Failed to request trust. You will likely be prompted for the code again in the coming weeks")
    
    return api

def get_or_create_folder(drive, folder_name):
    """Get a folder object, creating it if it doesn't exist."""
    try:
        return drive[folder_name]
    except KeyError:
        print(f"Folder '{folder_name}' not found. Creating it...")
        # Note: pyicloud might not have a direct 'mkdir' on the drive root in some versions,
        # but usually drive['name'].mkdir() works on folders. 
        # For the root, we might need to check if it exists in drive.dir()
        # Let's try to create it if it's missing.
        # The API for creating a folder at root might be drive.mkdir(folder_name)
        # But let's verify the API capabilities. 
        # If drive is the root, it behaves like a folder.
        drive.mkdir(folder_name)
        return drive[folder_name]

def upload_file(drive_folder, local_path, relative_path):
    """Upload a single file to the iCloud Drive folder."""
    filename = os.path.basename(local_path)
    
    # Check if file already exists
    # This is a simple check. For more robustness we could check size/date if available.
    # pyicloud file objects have 'size', 'dateModified', etc.
    
    try:
        icloud_file = drive_folder[filename]
        print(f"File '{relative_path}' already exists. Overwriting...")
        # To overwrite, we usually delete and re-upload or just upload (depending on API).
        # PyiCloud upload usually handles it or we might need to delete first.
        # Let's try deleting first to be safe.
        icloud_file.delete()
    except KeyError:
        pass # File doesn't exist, proceed to upload

    print(f"Uploading '{relative_path}'...")
    with open(local_path, 'rb') as f:
        retry_api_call(drive_folder.upload, f)

def sync_directory(api, local_root, icloud_folder):
    """Recursively sync local directory to iCloud folder."""
    
    # 1. Upload files in current directory
    with os.scandir(local_root) as it:
        for entry in it:
            if entry.name.startswith('.') or entry.name == '__pycache__':
                continue
            
            if entry.is_file():
                relative_path = os.path.relpath(entry.path, os.getcwd())
                
                # Check if file is in a whitelisted directory
                if not any(relative_path.startswith(f + os.sep) for f in FOLDERS_TO_SYNC):
                    # print(f"Skipping non-whitelisted file: {relative_path}")
                    continue

                upload_file(icloud_folder, entry.path, relative_path)
            
            elif entry.is_dir():
                # Only sync subdirectories if we are at the root and they are in the whitelist
                # OR if we are already inside a whitelisted directory (recursive)
                
                # Determine if we should process this directory
                process_dir = False
                rel_path = os.path.relpath(entry.path, os.getcwd())
                
                # Check if this dir is a top-level whitelisted dir
                if rel_path in FOLDERS_TO_SYNC:
                    process_dir = True
                # Check if we are already inside a whitelisted dir
                elif any(rel_path.startswith(f + os.sep) for f in FOLDERS_TO_SYNC):
                    process_dir = True
                
                if not process_dir:
                    continue

                dir_name = entry.name
                print(f"Processing directory: {dir_name}")
                
                # Get or create subdirectory in iCloud
                try:
                    sub_folder = icloud_folder[dir_name]
                except KeyError:
                    print(f"Creating folder '{dir_name}' in iCloud...")
                    # mkdir returns a dict with 'folders' list containing the new node data
                    result = retry_api_call(icloud_folder.mkdir, dir_name)
                    
                    try:
                        new_folder_data = result['folders'][0]
                        sub_folder = DriveNode(icloud_folder.connection, new_folder_data)
                    except (KeyError, IndexError, TypeError) as e:
                        print(f"CRITICAL: Failed to parse mkdir result for '{dir_name}': {e}. Falling back to fetch.")
                        # Fallback to fetch if parsing fails
                        try:
                            sub_folder = icloud_folder[dir_name]
                        except KeyError:
                             # Try one refresh
                            print("Folder not found immediately. Refreshing list...")
                            try:
                                icloud_folder.dir()
                            except:
                                pass
                            sub_folder = icloud_folder[dir_name]
                
                print(f"Entering directory: {dir_name}")
                sync_directory(api, entry.path, sub_folder)

def main():
    # Check for username env var or prompt? 
    # PyiCloudService can take username as arg, or prompt if not provided.
    # We'll let it handle it, but it's better if the user sets ICLOUD_USERNAME env var.
    
    api = get_icloud_service()
    print("Authenticated successfully.")

    drive = api.drive
    print("Accessing iCloud Drive...")

    # Get target root folder
    app_folder = get_or_create_folder(drive, ICLOUD_FOLDER_NAME)
    
    print(f"Deploying to iCloud Drive folder: {ICLOUD_FOLDER_NAME}")
    
    current_hash = get_current_commit_hash()
    print(f"Current commit: {current_hash}")
    
    last_hash = get_last_deployed_commit(app_folder)
    
    if last_hash:
        print(f"Found last deployed commit: {last_hash}")
        print("Calculating incremental changes...")
        changes = get_git_changes(last_hash, current_hash)
        
        if not changes:
            print("No changes detected since last deploy.")
        else:
            print(f"Found {len(changes)} changed files.")
            for status, path in changes:
                # Check whitelist
                if not any(path.startswith(f + os.sep) for f in FOLDERS_TO_SYNC):
                    # print(f"Skipping non-whitelisted file: {path}")
                    continue
                
                if status == 'D':
                    delete_file(app_folder, path)
                else:
                    # Added or Modified
                    if os.path.exists(path):
                        # We need to handle folder creation for the path
                        # Logic is similar to sync_directory but for a single file
                        # Let's reuse sync logic or simple upload?
                        # We need to ensure parent dirs exist.
                        
                        # Split path to get dirs
                        dirs = os.path.dirname(path).split(os.sep)
                        current_node = app_folder
                        
                        # Traverse/Create dirs
                        valid_path = True
                        for d in dirs:
                            if not d: continue
                            try:
                                current_node = current_node[d]
                            except KeyError:
                                print(f"Creating folder '{d}'...")
                                result = retry_api_call(current_node.mkdir, d)
                                try:
                                    new_data = result['folders'][0]
                                    current_node = DriveNode(current_node.connection, new_data)
                                except:
                                    # Fallback
                                    try:
                                        current_node = current_node[d]
                                    except KeyError:
                                        # Try refresh
                                        try: current_node.dir() 
                                        except: pass
                                        current_node = current_node[d]

                        upload_file(current_node, path, path)
                    else:
                        print(f"Warning: File '{path}' marked as changed but not found locally.")
            
            update_last_deployed_commit(app_folder, current_hash)
            
    else:
        print("No previous deployment found (or state file missing). Performing full sync...")
        # Start sync from current directory
        current_dir = os.getcwd()
        sync_directory(api, current_dir, app_folder)
        update_last_deployed_commit(app_folder, current_hash)
    
    print("Deployment complete!")

if __name__ == "__main__":
    main()
