import os

def validate_file_path(file_path):
    # Check if the path is absolute or relative
    if not os.path.exists(file_path):
        raise ValueError(f"File path '{file_path}' does not exist.")
    return file_path