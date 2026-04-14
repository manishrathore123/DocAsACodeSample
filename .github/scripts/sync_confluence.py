import os
import sys
import hashlib
import re
from atlassian import Confluence
import markdown

# --- Configuration from environment ---
CONFLUENCE_URL = os.environ.get('CONFLUENCE_URL')
CONFLUENCE_USERNAME = os.environ.get('CONFLUENCE_USERNAME')
CONFLUENCE_API_TOKEN = os.environ.get('CONFLUENCE_API_TOKEN')
CONFLUENCE_SPACE_KEY = os.environ.get('CONFLUENCE_SPACE_KEY')
CONFLUENCE_PARENT_PAGE_ID = os.environ.get('CONFLUENCE_PARENT_PAGE_ID')
CONFLUENCE_ARCHIVE_PARENT_PAGE_ID = os.environ.get('CONFLUENCE_ARCHIVE_PARENT_PAGE_ID')

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
    """
    Converts Markdown to Confluence Storage Format, with special handling for Mermaid diagrams.
    This version uses a splitting method to avoid complex regex and string formatting issues.
    """
    # Define the start and end fences for a mermaid block
    mermaid_start_fence = '```mermaid'
    mermaid_end_fence = '```'

    # Split the document into parts based on the mermaid start fence
    parts = md_content.split(mermaid_start_fence)

    final_html = ""

    # The first part is always standard markdown
    final_html += markdown.markdown(parts[0])

    # Process subsequent parts
    for part in parts[1:]:
        # Each part will contain a diagram and then more markdown
        try:
            diagram_code, remaining_md = part.split(mermaid_end_fence, 1)
        except ValueError:
            # This handles the case of a missing closing fence
            diagram_code = part
            remaining_md = ""

        # Trim the diagram code
        diagram_code = diagram_code.strip()

        # Create the Confluence macro for the mermaid diagram
        confluence_macro = (
            '<ac:structured-macro ac:name="mermaid">'
            + '<ac:plain-text-body><![CDATA['
            + diagram_code
            + ']]></ac:plain-text-body>'
            + '</ac:structured-macro>'
        )
        final_html += confluence_macro

        # Convert the remaining markdown part to HTML
        final_html += markdown.markdown(remaining_md)

    return f'<div class="markdown-body">{final_html}</div>'

def find_page_in_space_by_title(title: str):
    try:
        page = confluence.get_page_by_title(
            space=CONFLUENCE_SPACE_KEY, title=title, expand='ancestors,body.storage,version'
        )
        return page
    except Exception:
        return None

def ensure_folder_page(folder_title: str, parent_id: str) -> str:
    existing = find_page_in_space_by_title(folder_title)
    if existing:
        page_id = existing['id']
        current_parent_id = existing['ancestors'][-1]['id'] if existing.get('ancestors') else None
        if str(current_parent_id) != str(parent_id):
            print(f"Moving folder page '{folder_title}' to parent {parent_id}.")
            body_content = existing.get('body', {}).get('storage', {}).get('value', '')
            confluence.update_page(page_id=page_id, title=folder_title, body=body_content, parent_id=parent_id)
        return page_id
    print(f"Creating folder page '{folder_title}' under parent {parent_id}.")
    created = confluence.create_page(space=CONFLUENCE_SPACE_KEY, parent_id=parent_id, title=folder_title, body="", representation="storage")
    return created["id"]

def main():
    if not all([CONFLUENCE_URL, CONFLUENCE_USERNAME, CONFLUENCE_API_TOKEN, CONFLUENCE_SPACE_KEY, CONFLUENCE_PARENT_PAGE_ID]):
        print("Error: Missing required env vars."); sys.exit(1)

    print(f"Starting sync...")
    if CONFLUENCE_ARCHIVE_PARENT_PAGE_ID:
        print(f"Archiving enabled.")
        try: confluence.get_page_by_id(CONFLUENCE_ARCHIVE_PARENT_PAGE_ID)
        except Exception: print(f"Error: Archive parent page ID not found."); sys.exit(1)
    else:
        print(f"Archiving disabled.")

    folder_parent_ids = {"": CONFLUENCE_PARENT_PAGE_ID}
    if os.path.isdir(DOCS_FOLDER):
        for root, dirs, files in os.walk(DOCS_FOLDER):
            rel = os.path.relpath(root, DOCS_FOLDER)
            folder_path = "" if rel == "." else rel.replace("\\", "/")
            parent_id = folder_parent_ids[folder_path]
            for d in dirs:
                sub_path = os.path.join(folder_path, d).replace("\\", "/")
                if sub_path in folder_parent_ids: continue
                folder_page_id = ensure_folder_page(to_title(d), parent_id)
                folder_parent_ids[sub_path] = folder_page_id

    local_pages = {}
    if os.path.isdir(DOCS_FOLDER):
        for root, _, files in os.walk(DOCS_FOLDER):
            rel = os.path.relpath(root, DOCS_FOLDER)
            folder_path = "" if rel == "." else rel.replace("\\", "/")
            parent_id = folder_parent_ids[folder_path]
            for filename in files:
                if not filename.endswith(".md"): continue
                filepath = os.path.join(root, filename)
                with open(filepath, "r", encoding="utf-8") as f: md_content = f.read()
                title = (
                    "Documentation Home" if folder_path == "" and os.path.splitext(filename)[0].lower() == "index" else
                    to_title(os.path.basename(folder_path)) if os.path.splitext(filename)[0].lower() == "index" else
                    to_title(os.path.splitext(filename)[0])
                )
                key = (parent_id, title)
                storage = markdown_to_storage(md_content)
                local_pages[key] = {"title": title, "storage": storage, "hash": md5(storage), "parent_id": parent_id, "filepath": filepath}

    remote_pages = {}
    start, limit = 0, 200
    while True:
        try:
            chunk = confluence.get_all_pages_from_space(CONFLUENCE_SPACE_KEY, start=start, limit=limit, expand='ancestors,body.storage,version')
            if not chunk: break
            for page in chunk:
                parent_id = page['ancestors'][-1]['id'] if page.get('ancestors') else None
                remote_pages[(parent_id, page['title'])] = {"id": page['id'], "title": page['title'], "hash": md5(page.get('body',{}).get('storage',{}).get('value','')), "version": page['version']['number']}
            if len(chunk) < limit: break
            start += limit
        except Exception as e: print(f"Error fetching pages: {e}"); sys.exit(1)

    to_create, to_update, to_archive_or_delete = [], [], []
    for key, local in local_pages.items():
        remote = remote_pages.get(key)
        if not remote:
            existing_anywhere = find_page_in_space_by_title(local['title'])
            if existing_anywhere:
                to_update.append({"id": existing_anywhere['id'], **local, "action": "move"})
            else:
                to_create.append(local)
        elif local['hash'] != remote['hash']:
            to_update.append({"id": remote['id'], **local, "action": "update"})

    for remote_key, remote in remote_pages.items():
        page_id, title = remote['id'], remote['title']
        if str(page_id) == str(CONFLUENCE_PARENT_PAGE_ID) or (CONFLUENCE_ARCHIVE_PARENT_PAGE_ID and str(page_id) == str(CONFLUENCE_ARCHIVE_PARENT_PAGE_ID)):
            continue
        if remote_key not in local_pages.keys():
            is_folder = page_id in folder_parent_ids.values()
            if is_folder and confluence.get_child_pages(page_id):
                print(f"Skipping archive of FOLDER page '{title}' (ID {page_id}) as it has children.")
                continue
            to_archive_or_delete.append(remote)

    for p in to_create:
        print(f"Creating page '{p['title']}' under parent {p['parent_id']}.")
        try: confluence.create_page(space=CONFLUENCE_SPACE_KEY, parent_id=p["parent_id"], title=p["title"], body=p["storage"], representation="storage")
        except Exception as e: print(f"Error creating page: {e}")

    for p in to_update:
        print(f"Updating page '{p['title']}' (ID {p['id']}) - Action: {p['action']}.")
        try: 
            confluence.update_page(page_id=p["id"], title=p["title"], body=p["storage"], parent_id=p["parent_id"])
        except Exception as e: print(f"Error updating page: {e}")

    for p in to_archive_or_delete:
        page_title, page_id = p['title'], p['id']
        if CONFLUENCE_ARCHIVE_PARENT_PAGE_ID:
            print(f"Archiving page '{page_title}' (ID {page_id}).")
            try:
                current_body = confluence.get_page_by_id(page_id, expand='body.storage')['body']['storage']['value']
                confluence.update_page(page_id=page_id, title=page_title, body=current_body, parent_id=CONFLUENCE_ARCHIVE_PARENT_PAGE_ID)
            except Exception as e:
                print(f"Error archiving page: {e}")
        else:
            print(f"Deleting page '{page_title}' (ID {page_id}).")
            try: confluence.remove_page(page_id=p["id"], recursive=False)
            except Exception as e: print(f"Error deleting page: {e}")

    # --- 7. Summary ---
    print("\n========== Sync Summary ==========")
    print(f"Pages created: {len(to_create)}")
    print(f"Pages updated/moved: {len(to_update)}")
    print(f"Pages archived/deleted: {len(to_archive_or_delete)}")
    print("===================================")
    print("Sync complete.")

if __name__ == "__main__":
    main()
