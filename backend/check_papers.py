from app import app, Paper

with app.app_context():
    papers = Paper.query.all()
    for p in papers:
        print(f'ID: {p.id}, Title: {p.title}, File Path: {p.file_path}')
