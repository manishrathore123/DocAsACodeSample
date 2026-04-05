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

# ---------- helper: ensure a page exists under parent ----------

def ensure_page(title: str, parent_id: str) -> str:
    """
    Return page_id of page with given title under parent_id,
    creating it if necessary (empty body).
    """
    children = confluence.get_child_pages(parent_id)
    for c in children:
        if c["title"] == title:
            return c["id"]

    print(f"Creating folder/parent page '{title}' under parent {parent_id}")
    created = confluence.create_page(
        space=CONFLUENCE_SPACE_KEY,
        parent_id=parent_id,
        title=title,
        body="",
        representation="storage",
    )
    return created["id"]

# ---------- main ----------

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

    # 1) Build mapping: folder_path -> parent_page_id in Confluence
    #
    # folder_path examples:
    #   ""          -> CONFLUENCE_PARENT_PAGE_ID  (docs root)
    #   "inner"     -> page "Inner" under root parent
    #   "inner2"    -> page "Inner2" under root parent
    #
    folder_parent_ids = {"": CONFLUENCE_PARENT_PAGE_ID}

    if os.path.isdir(DOCS_FOLDER):
        for root, dirs, files in os.walk(DOCS_FOLDER):
            rel = os.path.relpath(root, DOCS_FOLDER)  # '.' or 'inner' or 'inner2'
            if rel == ".":
                folder_path = ""
            else:
                folder_path = rel.replace("\\", "/")

            # ensure page for each immediate subfolder (one level at a time)
            parent_path = folder_path
            parent_id = folder_parent_ids[parent_path]

            for d in dirs:
                sub_folder_path = os.path.join(folder_path, d).replace("\\", "/")
                if sub_folder_path in folder_parent_ids:
                    continue
                folder_title = to_title(d)
                folder_page_id = ensure_page(folder_title, parent_id)
                folder_parent_ids[sub_folder_path] = folder_page_id

    # After this:
    #   folder_parent_ids[""]        -> root parent ID
    #   folder_parent_ids["inner"]   -> page "Inner"
    #   folder_parent_ids["inner2"]  -> page "Inner2"

    # 2) Build map of existing pages by (parent_id, title) for update/delete
    existing_pages = {}  # (parent_id, title) -> dict(id, hash)

    def index_existing(parent_id: str):
        children = confluence.get_child_pages(parent_id)
        for c in children:
            detail = confluence.get_page_by_id(c["id"], expand="body.storage,version")
            storage = detail.get("body", {}).get("storage", {}).get("value", "")
            existing_pages[(parent_id, c["title"])] = {
                "id": c["id"],
                "hash": md5(storage),
                "version": detail.get("version", {}).get("number", 1),
            }
            # recurse down to keep delete logic safe
            index_existing(c["id"])

    index_existing(CONFLUENCE_PARENT_PAGE_ID)

    # 3) Discover local pages
    local_keys = set()
    pages_to_create = []
    pages_to_update = []

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
                # Root index.md -> Documentation Home
                if folder_path == "" and name_no_ext.lower() == "index":
                    title = "Documentation Home"
                elif name_no_ext.lower() == "index":
                    # index.md inside a folder updates the folder page itself
                    title = to_title(os.path.basename(folder_path))
                else:
                    title = to_title(name_no_ext)

                html = markdown.markdown(md_content)
                storage = f'<div class="markdown-body">{html}</div>'
                content_hash = md5(storage)

                key = (parent_id, title)
                local_keys.add(key)

                if key not in existing_pages:
                    pages_to_create.append(
                        {
                            "parent_id": parent_id,
                            "title": title,
                            "storage": storage,
                            "filepath": filepath,
                        }
                    )
                else:
                    remote = existing_pages[key]
                    if remote["hash"] != content_hash:
                        pages_to_update.append(
                            {
                                "id": remote["id"],
                                "parent_id": parent_id,
                                "title": title,
                                "storage": storage,
                                "filepath": filepath,
                                "version": remote["version"],
                            }
                        )
                    else:
                        print(f"Up to date: {filepath} -> {title}")

    # 4) Determine deletions: existing pages that have no local counterpart,
    #    but only leaf pages (to avoid deleting folder parents that still have children)
    existing_keys = set(existing_pages.keys())
    keys_to_delete = existing_keys - local_keys
    pages_to_delete = []

    # find children so we don't delete parents with children
    parent_has_children = set()
    for (parent_id, title), info in existing_pages.items():
        # each page is a parent of some children in existing_pages
        # unfortunately we don't have reverse index, so we approximate:
        # any page that appears as parent_id in a key gets marked
        pass
    # simpler: fetch children again per candidate and see if it has children
    for key in keys_to_delete:
        parent_id, title = key
        page_info = existing_pages[key]
        children = confluence.get_child_pages(page_info["id"])
        if children:
            # has children, skip deleting
            continue
        pages_to_delete.append(
            {"id": page_info["id"], "title": title, "parent_id": parent_id}
        )

    # 5) Apply changes

    # Creates
    for p in pages_to_create:
        print(
            f"Creating page '{p['title']}' under parent {p['parent_id']} from {p['filepath']}"
        )
        try:
            confluence.create_page(
                space=CONFLUENCE_SPACE_KEY,
                parent_id=p["parent_id"],
                title=p["title"],
                body=p["storage"],
                representation="storage",
            )
        except Exception as e:
            print(f"Error creating page '{p['title']}': {e}")

    # Updates
    for p in pages_to_update:
        print(
            f"Updating page '{p['title']}' (ID {p['id']}) from {p['filepath']}"
        )
        try:
            confluence.update_page(
                page_id=p["id"],
                title=p["title"],
                body=p["storage"],
                representation="storage",
                version=p["version"] + 1,
            )
        except Exception as e:
            print(f"Error updating page '{p['title']}': {e}")

    # Deletes
    for p in pages_to_delete:
        print(
            f"Deleting page '{p['title']}' (ID {p['id']}) – no local markdown found"
        )
        try:
            confluence.remove_page(page_id=p["id"], recursive=False)
        except Exception as e:
            print(f"Error deleting page '{p['title']}': {e}")

    print("Sync complete.")

if __name__ == "__main__":
    main()
