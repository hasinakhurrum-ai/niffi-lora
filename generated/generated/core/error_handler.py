class ErrorHandler:
    @staticmethod
    def handle_error(error_message):
        if not isinstance(error_message, str):
            raise ValueError("Error message must be a string")

        print(f"Error occurred: {error_message}")