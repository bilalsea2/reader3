import os
import pickle
from functools import lru_cache
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from reader3 import Book, BookMetadata, ChapterContent, TOCEntry, process_epub, save_to_pickle

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Where are the book folders located?
BOOKS_DIR = "library"

if not os.path.exists(BOOKS_DIR):
    os.makedirs(BOOKS_DIR)
    print(f"Created library directory: {BOOKS_DIR}")

@lru_cache(maxsize=10)
def load_book_cached(folder_name: str) -> Optional[Book]:
    """
    Loads the book from the pickle file.
    Cached so we don't re-read the disk on every click.
    """
    file_path = os.path.join(BOOKS_DIR, folder_name, "book.pkl")
    if not os.path.exists(file_path):
        return None

    try:
        # Ensure Book class is available in the pickle context
        import sys
        import reader3
        # Make Book available as both __main__.Book and reader3.Book for pickle compatibility
        if '__main__' not in sys.modules or not hasattr(sys.modules['__main__'], 'Book'):
            import types
            if '__main__' not in sys.modules:
                sys.modules['__main__'] = types.ModuleType('__main__')
            sys.modules['__main__'].Book = Book
            sys.modules['__main__'].BookMetadata = BookMetadata
            sys.modules['__main__'].ChapterContent = ChapterContent
            sys.modules['__main__'].TOCEntry = TOCEntry
        
        with open(file_path, "rb") as f:
            book = pickle.load(f)
        return book
    except Exception as e:
        print(f"Error loading book {folder_name}: {e}")
        import traceback
        traceback.print_exc()
        return None

@app.get("/", response_class=HTMLResponse)
async def library_view(request: Request):
    """Lists all available processed books."""
    books = []

    # Scan library directory for folders ending in '_data' that have a book.pkl
    if os.path.exists(BOOKS_DIR):
        for item in os.listdir(BOOKS_DIR):
            item_path = os.path.join(BOOKS_DIR, item)
            if item.endswith("_data") and os.path.isdir(item_path):
                # Try to load it to get the title
                book = load_book_cached(item)
                if book:
                    books.append({
                        "id": item,
                        "title": book.metadata.title,
                        "author": ", ".join(book.metadata.authors),
                        "chapters": len(book.spine)
                    })

    return templates.TemplateResponse("library.html", {"request": request, "books": books})

@app.get("/read/{book_id}", response_class=HTMLResponse)
async def redirect_to_first_chapter(book_id: str):
    """Helper to just go to chapter 0."""
    return await read_chapter(book_id=book_id, chapter_index=0)

@app.get("/read/{book_id}/{chapter_index}", response_class=HTMLResponse)
async def read_chapter(request: Request, book_id: str, chapter_index: int):
    """The main reader interface."""
    book = load_book_cached(book_id)
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")

    if chapter_index < 0 or chapter_index >= len(book.spine):
        raise HTTPException(status_code=404, detail="Chapter not found")

    current_chapter = book.spine[chapter_index]

    # Calculate Prev/Next links
    prev_idx = chapter_index - 1 if chapter_index > 0 else None
    next_idx = chapter_index + 1 if chapter_index < len(book.spine) - 1 else None

    return templates.TemplateResponse("reader.html", {
        "request": request,
        "book": book,
        "current_chapter": current_chapter,
        "chapter_index": chapter_index,
        "book_id": book_id,
        "prev_idx": prev_idx,
        "next_idx": next_idx
    })

@app.get("/read/{book_id}/images/{image_name}")
async def serve_image(book_id: str, image_name: str):
    """
    Serves images specifically for a book.
    The HTML contains <img src="images/pic.jpg">.
    The browser resolves this to /read/{book_id}/images/pic.jpg.
    """
    # Security check: ensure book_id is clean
    safe_book_id = os.path.basename(book_id)
    safe_image_name = os.path.basename(image_name)

    img_path = os.path.join(BOOKS_DIR, safe_book_id, "images", safe_image_name)

    if not os.path.exists(img_path):
        raise HTTPException(status_code=404, detail="Image not found")

    return FileResponse(img_path)

@app.post("/import", response_class=HTMLResponse)
async def import_book(request: Request, file: UploadFile = File(...)):
    """Import an EPUB file and process it."""
    # Validate file extension
    if not file.filename.endswith('.epub'):
        raise HTTPException(status_code=400, detail="Only EPUB files are supported")
    
    # Create library directory if it doesn't exist
    if not os.path.exists(BOOKS_DIR):
        os.makedirs(BOOKS_DIR)
    
    # Save uploaded file temporarily
    temp_path = os.path.join(BOOKS_DIR, file.filename)
    try:
        with open(temp_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Process the EPUB
        book_basename = os.path.splitext(file.filename)[0]
        out_dir = os.path.join(BOOKS_DIR, book_basename + "_data")
        
        print(f"Processing {file.filename}...")
        book_obj = process_epub(temp_path, out_dir)
        save_to_pickle(book_obj, out_dir)
        
        # Clean up temporary EPUB file
        os.remove(temp_path)
        
        # Clear cache so new book appears
        load_book_cached.cache_clear()
        
        print(f"Successfully imported: {book_obj.metadata.title}")
        
        # Redirect to library
        return RedirectResponse(url="/", status_code=303)
        
    except Exception as e:
        # Clean up on error
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise HTTPException(status_code=500, detail=f"Error importing book: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    print("Starting server at http://127.0.0.1:8123")
    uvicorn.run(app, host="127.0.0.1", port=8123)
