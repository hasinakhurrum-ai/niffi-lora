import os

def get_absolute_path(relative_path):
    """
    Returns the absolute path of a given relative path.
    
    Args:
    relative_path (str): The relative path to convert to an absolute path.
    
    Returns:
    str: The absolute path of the given relative path.
    """
    try:
        return os.path.abspath(relative_path)
    except Exception as e:
        print(f"Error getting absolute path: {e}")
        return None

def check_file_exists(file_path):
    """
    Checks if a file exists at the specified path.
    
    Args:
    file_path (str): The path to check for the existence of a file.
    
    Returns:
    bool: True if the file exists, False otherwise.
    """
    try:
        return os.path.isfile(file_path)
    except Exception as e:
        print(f"Error checking file existence: {e}")
        return None

def list_files_in_directory(directory_path):
    """
    Lists all files in a given directory.
    
    Args:
    directory_path (str): The path to the directory to list files from.
    
    Returns:
    list: A list of file names in the specified directory.
    """
    try:
        return os.listdir(directory_path)
    except Exception as e:
        print(f"Error listing files in directory: {e}")
        return None