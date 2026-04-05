import os
import sys
import hashlib
from atlassian import Confluence
import markdown

# --- Configuration from environment ---
CONFLUENCE_URL = os.environ.get('CONFLUENCE_URL')
CONFLUENCE_USERNAME = os.environ.get('CONFLUENCE_USERNAME')
CONFLUENCE_API_TOKEN = os.environ.get('CONFLUENCE_API_TOKEN')
CONFLUENCE_SPACE_KEY = os.environ.get('CONFLUENCE_SPACE_KEY')
CONFLUENCE_PARENT_PAGE_ID = os.environ.get('CONFLUENCE_PARENT_PAGE_ID')

DOCS_FOLDER = 'docs'  # root folder in repo

# --- Confluence client ---
confluence = Confluence(
    url=CONFLUENCE_URL,
    username=CONFLUENCE_USERNAME,
    password=CONFLUENCE_API_TOKEN,
    cloud=True
)

def md5(content: str) -> str:
    return hashlib.md5(content.encode('utf-8')).hexdigest()

def to_title(name: str) -> str:
    """Convert folder/file name to human title."""
    return (
        name.replace('-', ' ')
            .replace('_', ' ')
            .strip()
            .title()
    )

def get_existing_tree(root_parent_id: str):
    """
    Build a map of all existing pages under the root parent, by (path_key -> page_info).
    path_key is a string like 'ROOT/Inner' or 'ROOT/Inner/Getting Started Copy 4'.
    """
    tree = {}

    def walk(parent_id: str, current_path: str):
        children = confluence.get_child_pages(parent_id)
        for c in children:
            title = c['title']
            path_key = f"{current_path}/{title}" if current_path else title

            detail = confluence.get_page_by_id(c['id'], expand='body.storage')
            storage = detail.get('body', {}).get('storage', {}).get('value', '')
            page_hash = md5(storage)

            tree[path_key] = {
                'id': c['id'],
                'title': title,
                'hash': page_hash,
                'parent_path': current_path,
                'parent_id': parent_id
            }

            walk(c['id'], path_key)

    root_path_key = 'ROOT'
    walk(root_parent_id, root_path_key)
    return tree

def get_or_create_page(title: str, parent_id: str, parent_path_key: str, existing_tree: dict):
    """
    Get a page by title under a specific parent. If it doesn't exist, create it.
    Update existing_tree in place and return (page_id, path_key).
    """
    # look at children of this parent
    children = confluence.get_child_pages(parent_id)
    for c in children:
        if c['title'] == title:
            detail = confluence.get_page_by_id(c['id'], expand='body.storage')
            storage = detail.get('body', {}).get('storage', {}).get('value', '')
            page_hash = md5(storage)
            path_key = f"{parent_path_key}/{title}"
            existing_tree[path_key] = {
                'id': c['id'],
                'title': title,
                'hash': page_hash,
                'parent_path': parent_path_key,
                'parent_id': parent_id
            }
            return c['id'], path_key

    # create
    print(f"Creating folder/parent page '{title}' under parent {parent_id}")
    created = confluence.create_page(
        space=CONFLUENCE_SPACE_KEY,
        parent_id=parent_id,
        title=title,
        body='',
        representation='storage'
    )
    path_key = f"{parent_path_key}/{title}"
    existing_tree[path_key] = {
        'id': created['id'],
        'title': created['title'],
        'hash': md5(''),
        'parent_path': parent_path_key,
        'parent_id': parent_id
    }
    return created['id'], path_key

def main():
    if not all([CONFLUENCE_URL, CONFLUENCE_USERNAME, CONFLUENCE_API_TOKEN,
                CONFLUENCE_SPACE_KEY, CONFLUENCE_PARENT_PAGE_ID]):
        print("Missing one or more Confluence env vars.")
        sys.exit(1)

    print(f"Syncing '{DOCS_FOLDER}' into Confluence space '{CONFLUENCE_SPACE_KEY}' "
          f"under parent page ID '{CONFLUENCE_PARENT_PAGE_ID}'")

    # 1. Existing pages tree
    existing_tree = get_existing_tree(CONFLUENCE_PARENT_PAGE_ID)

    local_pages = {}  # path_key -> page data

    if not os.path.exists(DOCS_FOLDER):
        print(f"Warning: '{DOCS_FOLDER}' not found.")
    else:
        for root, dirs, files in os.walk(DOCS_FOLDER):
            rel_dir = os.path.relpath(root, DOCS_FOLDER)  # '.' or 'inner' or 'inner/inner2'
            if rel_dir == '.':
                folder_parts = []
            else:
                folder_parts = rel_dir.split(os.sep)

            parent_path_key = 'ROOT'
            parent_id = CONFLUENCE_PARENT_PAGE_ID

            # ensure folder pages exist for each part (e.g. 'inner', 'inner2')
            for part in folder_parts:
                folder_title = to_title(part)
                folder_path_key = f"{parent_path_key}/{folder_title}"

                if folder_path_key in existing_tree:
                    parent_id = existing_tree[folder_path_key]['id']
                else:
                    parent_id, folder_path_key = get_or_create_page(
                        folder_title,
                        parent_id,
                        parent_path_key,
                        existing_tree
                    )

                parent_path_key = folder_path_key

            # now handle files in this directory
            for filename in files:
                if not filename.endswith('.md'):
                    continue
                filepath = os.path.join(root, filename)
                with open(filepath, 'r', encoding='utf-8') as f:
                    md_content = f.read()

                name_no_ext = os.path.splitext(filename)[0]
                if name_no_ext.lower() == 'index':
                    # index.md represents folder page itself
                    if folder_parts:
                        page_title = to_title(folder_parts[-1])
                    else:
                        page_title = "Documentation Home"
                    page_path_key = f"{parent_path_key}/{page_title}"
                else:
                    page_title = to_title(name_no_ext)
                    page_path_key = f"{parent_path_key}/{page_title}"

                html_content = markdown.markdown(md_content)
                storage = f'<div class="markdown-body">{html_content}</div>'
                storage_hash = md5(storage)

                local_pages[page_path_key] = {
                    'title': page_title,
                    'storage': storage,
                    'hash': storage_hash,
                    'parent_path': parent_path_key,
                    'parent_id': parent_id,
                    'filepath': filepath
                }

    # 3. Compare local vs remote
    pages_to_create = []
    pages_to_update = []

    for path_key, local in local_pages.items():
        if path_key not in existing_tree:
            pages_to_create.append(local)
        else:
            remote = existing_tree[path_key]
            if local['hash'] != remote['hash']:
                pages_to_update.append({
                    'id': remote['id'],
                    'title': local['title'],
                    'storage': local['storage'],
                    'filepath': local['filepath'],
                    'parent_id': remote['parent_id'],
                    'path_key': path_key
                })
            else:
                print(f"Up to date: {path_key}")

    # pages to delete: remote pages with no local counterpart, but only leaves
    pages_to_delete = []
    for path_key, remote in existing_tree.items():
        if path_key == 'ROOT':
            continue
        if path_key not in local_pages:
            is_parent = any(
                child['parent_path'] == path_key
                for child in existing_tree.values()
            )
            if not is_parent:
                pages_to_delete.append(remote)

    # 4. Apply changes
    for p in pages_to_create:
        print(f"Creating page '{p['title']}' under parent {p['parent_id']} from {p['filepath']}")
        try:
            confluence.create_page(
                space=CONFLUENCE_SPACE_KEY,
                parent_id=p['parent_id'],
                title=p['title'],
                body=p['storage'],
                representation='storage'
            )
        except Exception as e:
            print(f"Error creating page '{p['title']}': {e}")

    for p in pages_to_update:
        print(f"Updating page '{p['title']}' (ID {p['id']}) from {p['filepath']}")
        try:
            detail = confluence.get_page_by_id(p['id'], expand='version')
            current_ver = detail.get('version', {}).get('number', 1)
            confluence.update_page(
                page_id=p['id'],
                title=p['title'],
                body=p['storage'],
                representation='storage',
                version=current_ver + 1
            )
        except Exception as e:
            print(f"Error updating page '{p['title']}': {e}")

    for p in pages_to_delete:
        print(f"Deleting page '{p['title']}' (ID {p['id']}) – no local markdown found")
        try:
            confluence.remove_page(page_id=p['id'], recursive=False)
        except Exception as e:
            print(f"Error deleting page '{p['title']}': {e}")

    print("Sync complete.")

if __name__ == "__main__":
    main()
