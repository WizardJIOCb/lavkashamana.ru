import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import Base, engine, SessionLocal
from app.models import Category
Base.metadata.create_all(engine)
db = SessionLocal()
for i, name in enumerate(['Чаи','Травы','Мёд','Масла']):
    slug = {'Чаи':'tea','Травы':'herbs','Мёд':'honey','Масла':'oils'}[name]
    if not db.query(Category).filter_by(slug=slug).first():
        db.add(Category(name=name, slug=slug, sort_order=i))
db.commit(); db.close()
print('Seed complete')
