#!/usr/bin/env python3
"""Consolidate all images from separate assets folders into a single images folder."""

import shutil
import re
from pathlib import Path

# Setup paths
outputs_dir = Path("outputs")
images_dir = outputs_dir / "images"

# Create images directory
images_dir.mkdir(exist_ok=True)
print(f"Created {images_dir}")

# Find all assets folders and move images
assets_folders = sorted([d for d in outputs_dir.iterdir() if d.is_dir() and "edited_assets" in d.name])

# Extract document number from folder name (e.g., "1. bekal haji edited_assets" -> 1)
image_mapping = {}  # maps old paths to new paths

for assets_folder in assets_folders:
    # Extract document number
    doc_num = assets_folder.name.split(".")[0].strip()
    
    # Move all images from this folder
    for image_file in sorted(assets_folder.iterdir()):
        if image_file.is_file() and image_file.suffix.lower() in [".png", ".jpg", ".jpeg", ".gif"]:
            # Create new filename with document number prefix
            new_name = f"{doc_num}_{image_file.name}"
            new_path = images_dir / new_name
            
            # Move file
            shutil.move(str(image_file), str(new_path))
            
            # Store mapping for later markdown updates
            # Old relative path: "X. bekal haji edited_assets/imageN.png"
            old_rel_path = f"{assets_folder.name}/{image_file.name}"
            # New relative path: "images/X_imageN.png"
            new_rel_path = f"images/{new_name}"
            image_mapping[old_rel_path] = new_rel_path
            
            print(f"Moved: {old_rel_path} -> {new_rel_path}")

# Update all markdown files with new image paths
md_files = outputs_dir.glob("*.md")

for md_file in md_files:
    content = md_file.read_text(encoding="utf-8")
    original_content = content
    
    # Replace all old image paths with new ones
    for old_path, new_path in image_mapping.items():
        # Handle both forward slashes and escaped paths in markdown
        content = content.replace(old_path, new_path)
    
    if content != original_content:
        md_file.write_text(content, encoding="utf-8")
        print(f"Updated: {md_file.name}")

# Remove empty assets folders
for assets_folder in assets_folders:
    if assets_folder.exists() and not any(assets_folder.iterdir()):
        assets_folder.rmdir()
        print(f"Removed empty folder: {assets_folder.name}")

print("\nConsolidation complete!")
print(f"Total images in {images_dir}: {len(list(images_dir.iterdir()))}")
