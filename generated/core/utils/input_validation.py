from typing import Any

def validate_input(input_data, expected_type):
    if not isinstance(input_data, expected_type):
        raise ValueError(f"Invalid input type. Expected {expected_type}, got {type(input_data)}")