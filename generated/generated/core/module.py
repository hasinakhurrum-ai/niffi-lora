import os

def validate_file_path(file_path):
    # Check if the file path is valid and exists
    if not os.path.exists(file_path):
        raise ValueError(f"File does not exist: {file_path}")

def get_valid_env_variable(env_var_name, default_value=None):
    # Retrieve environment variable with a default value if it's not set
    env_value = os.getenv(env_var_name)
    return env_value if env_value else default_value

# Example usage in your script
file_path = "/path/to/your/file.txt"
validate_file_path(file_path)

env_variable = get_valid_env_variable("MY_ENV_VAR", "default_value")