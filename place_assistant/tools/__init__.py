"""Plain Python function tools the root place_assistant agent can call.

The docstrings of these functions are sent to the model as tool
descriptions, so edit them with the same care as a prompt.
"""

from .output_files import convert_json_to_docx, read_output_file
from .web_fetch import fetch_rendered_page

__all__ = ["convert_json_to_docx", "fetch_rendered_page", "read_output_file"]
