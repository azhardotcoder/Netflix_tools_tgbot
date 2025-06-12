import os
import tempfile

async def combine_temp_files(file_paths):
    """
    Asynchronously combines content from a list of temporary file paths into a new temporary file.
    
    Args:
        file_paths (list): A list of paths to the temporary files to combine.
        
    Returns:
        tuple: A tuple containing:
            - str: The path to the new temporary file with combined content.
            - int: The number of unique lines.
            - int: The total number of lines read.
    """
    unique_lines = set()
    total_lines = 0
    
    for file_path in file_paths:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    stripped_line = line.strip()
                    if stripped_line:
                        unique_lines.add(stripped_line)
                        total_lines += 1
        except Exception:
            # Ignore errors for individual file reading
            pass
            
    # Create a new temporary file to store the combined, unique lines
    output_file = tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', suffix=".txt")
    
    try:
        for line in sorted(list(unique_lines)): # Sort for consistent output
            output_file.write(f"{line}\n")
    finally:
        output_file.close()

    return output_file.name, len(unique_lines), total_lines 