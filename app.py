import os
from api.index import app, DEFAULT_SAVE_DIR, manager

if __name__ == '__main__':
    # Initialize local directories and run server
    os.makedirs(DEFAULT_SAVE_DIR, exist_ok=True)
    saved = manager.load_saved_links()
    if saved:
        manager.links = saved
        manager.parts = manager.get_parts_from_links(saved)
    app.run(host='0.0.0.0', port=5000, debug=False)
