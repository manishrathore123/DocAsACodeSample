import os
import sys
import hashlib
from atlassian import Confluence
import markdown

# --- Config from env ---
CONFLUENCE_URL = os.environ.get('CONFLUENCE_URL')
CONFLUENCE_USERNAME = os.environ.get('CONFLUENCE_USERNAME')
CONFLUENCE_API_TOKEN = os.environ.get('CONFLUENCE_API_TOKEN')
CONFLUENCE_SPACE_KEY = os.environ.get('CONFLUENCE_SPACE_KEY')
CONFLUENCE_PARENT_PAGE_ID = os.environ.get('CONFLUENCE_PARENT_PAGE_ID')

DOCS_FOLDER = "docs"

confluence = Confluence(
    url=CONFLUENCE_URL,
    username=CONFLUENCE_USERNAME,
    password=CONFLUENCE_API_TOKEN,
    cloud=True,
)

def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()

def to_title(name: str) -> str:
    return name.replace("-", " ").replace("_", " ").strip().title()

def markdown_to_storage(md_content: str) -> str:
    html = markdown.markdown(md_content)
    return f'<div class="markdown-body">{html}</div>'

# --------- Helpers for Confluence ----------

def find_page_in_space_by_title(title: str):
    """Find first page in the space with given title."""
    results = confluence.get_page_id(CONFLUENCE_SPACE_KEY, title)
    if results:
        # get_page_id sometimes returns id directly, sometimes None if not found
        # If it's an int/str id:
        page_id = results
        try:
            page = confluence.get_page_by_id(page_id, expand='ancestors,body.storage,version')
            return page
        except Exception:
            return None
    return None

def ensure_folder_page(folder_title: str, parent_id: str) -> str:
    """
    Ensure there is a 'folder' page with given title under parent_id.
    Because titles are unique per space, we:
      - If page with that title exists anywhere, we move it under parent_id.
      - Otherwise, create a new page under parent_id.
    """
    existing = find_page_in_space_by_title(folder_title)
    if existing:
        page_id = existing['id']
        # Move it under parent_id if needed
        current_parent_id = existing['ancestors'][-1]['id'] if existing.get('ancestors') else None
        if current_parent_id != parent_id:
            print(f"Moving existing folder page '{folder_title}' (ID {page_id}) under parent {parent_id}")
            confluence.update_page(
                page_id=page_id,
                title=folder_title,
                body=existing['body']['storage']['value'],
                parent_id=parent_id,
            )
        return page_id

    # Create new folder page
    print(f"Creating new folder page '{folder_title}' under parent {parent_id}")
    created = confluence.create_page(
        space=CONFLUENCE_SPACE_KEY,
        parent_id=parent_id,
        title=folder_title,
        body="",
        representation="storage",
    )
    return created["id"]

# --------- Main sync ----------

def main():
    if not all(
        [
            CONFLUENCE_URL,
            CONFLUENCE_USERNAME,
            CONFLUENCE_API_TOKEN,
            CONFLUENCE_SPACE_KEY,
            CONFLUENCE_PARENT_PAGE_ID,
        ]
    ):
        print("Missing required Confluence env vars.")
        sys.exit(1)

    print(
        f"Syncing '{DOCS_FOLDER}' into space '{CONFLUENCE_SPACE_KEY}' "
        f"under parent page ID '{CONFLUENCE_PARENT_PAGE_ID}'"
    )

    # 1) Build mapping: folder_path -> parent_page_id
    #   ""       -> CONFLUENCE_PARENT_PAGE_ID
    #   "inner"  -> page 'Inner'
    #   "inner2" -> page 'Inner2'
    folder_parent_ids = {"": CONFLUENCE_PARENT_PAGE_ID}

    if os.path.isdir(DOCS_FOLDER):
        for root, dirs, files in os.walk(DOCS_FOLDER):
            rel = os.path.relpath(root, DOCS_FOLDER)  # "." or "inner" or "inner2"
            folder_path = "" if rel == "." else rel.replace("\\", "/")

            parent_folder_path = folder_path
            parent_id = folder_parent_ids[parent_folder_path]

            for d in dirs:
                sub_path = os.path.join(folder_path, d).replace("\\", "/")
                if sub_path in folder_parent_ids:
                    continue
                folder_title = to_title(d)
                folder_page_id = ensure_folder_page(folder_title, parent_id)
                folder_parent_ids[sub_path] = folder_page_id

    # 2) Process each .md file: create or move+update page
    if os.path.isdir(DOCS_FOLDER):
        for root, dirs, files in os.walk(DOCS_FOLDER):
            rel = os.path.relpath(root, DOCS_FOLDER)
            folder_path = "" if rel == "." else rel.replace("\\", "/")
            parent_id = folder_parent_ids[folder_path]

            for filename in files:
                if not filename.endswith(".md"):
                    continue

                filepath = os.path.join(root, filename)
                with open(filepath, "r", encoding="utf-8") as f:
                    md_content = f.read()

                name_no_ext = os.path.splitext(filename)[0]
                if folder_path == "" and name_no_ext.lower() == "index":
                    title = "Documentation Home"
                elif name_no_ext.lower() == "index":
                    title = to_title(os.path.basename(folder_path))
                else:
                    title = to_title(name_no_ext)

                storage = markdown_to_storage(md_content)
                content_hash = md5(storage)

                # Find existing page with this title anywhere in the space
                existing = find_page_in_space_by_title(title)

                if not existing:
                    # Create new page under correct parent
                    print(f"Creating page '{title}' under parent {parent_id} from {filepath}")
                    try:
                        confluence.create_page(
                            space=CONFLUENCE_SPACE_KEY,
                            parent_id=parent_id,
                            title=title,
                            body=storage,
                            representation="storage",
                        )
                    except Exception as e:
                        print(f"Error creating page '{title}': {e}")
                else:
                    page_id = existing["id"]
                    current_parent_id = (
                        existing["ancestors"][-1]["id"]
                        if existing.get("ancestors")
                        else None
                    )
                    remote_storage = existing["body"]["storage"]["value"]
                    remote_hash = md5(remote_storage)

                    # Decide if we need to move and/or update
                    needs_move = current_parent_id != parent_id
                    needs_update = remote_hash != content_hash

                    if not needs_move and not needs_update:
                        print(f"Up to date: {filepath} -> '{title}'")
                        continue

                    print(
                        f"Updating page '{title}' (ID {page_id}) "
                        f"{'(move parent)' if needs_move else ''} from {filepath}"
                    )
                    try:
                        confluence.update_page(
                            page_id=page_id,
                            title=title,
                            body=storage,
                            parent_id=parent_id,
                        )
                    except Exception as e:
                        print(f"Error updating page '{title}' (ID {page_id}): {e}")

    print("Sync complete.")

if __name__ == "__main__":
    main()
