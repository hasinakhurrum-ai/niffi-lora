import os

def validate_path(path):
    if not os.path.isabs(path):
        raise ValueError(f"Path '{path}' is not absolute.")
    if not os.access(path, os.R_OK | os.W_OK):
        raise PermissionError(f"Path '{path}' does not have the necessary read and write permissions.")

def safe_access_file(file_path):
    validate_path(file_path)
    try:
        with open(file_path, 'r') as file:
            content = file.read()
            return content
    except Exception as e:
        print(f"Error accessing file {file_path}: {e}")
        raise

def list_files_in_directory(directory_path):
    validate_path(directory_path)
    if os.path.exists(directory_path) and os.path.isdir(directory_path):
        try:
            files = os.listdir(directory_path)
            return files
        except Exception as e:
            print(f"Error listing files in directory {directory_path}: {e}")
            raise
    else:
        print(f"Directory {directory_path} does not exist or is not a directory.")
        raise