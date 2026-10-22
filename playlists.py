# playlists.py
"""Пользовательские плейлисты: хранение в JSON-файлах под data/playlists/."""

import os
import random
import re
import json
import tempfile
from datetime import datetime

from difflib import SequenceMatcher


PLAYLISTS_DIR = os.path.join("data", "playlists")


# ─── утилиты ──────────────────────────────────────────────────

def _sanitize_name(name: str) -> str:
    """Безопасное имя файла: буквы, цифры, пробелы, - и _."""
    if not name:
        return ""
    name = name.strip()
    name = re.sub(r'[^\w\s\-]', '', name, flags=re.UNICODE)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:50]


def _path_for(safe_name: str) -> str:
    return os.path.join(PLAYLISTS_DIR, f"{safe_name.lower()}.json")


def _ensure_dir():
    os.makedirs(PLAYLISTS_DIR, exist_ok=True)


def _atomic_write(path: str, data: dict):
    """Пишем во временный файл, потом os.replace. Не оставляет половинчатых файлов."""
    dir_ = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _read(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"⚠️ playlists: не читается {path}: {e}")
        return None


def _iter_all():
    """Генератор всех валидных плейлистов из папки."""
    if not os.path.isdir(PLAYLISTS_DIR):
        return
    for fname in sorted(os.listdir(PLAYLISTS_DIR)):
        if not fname.endswith(".json"):
            continue
        data = _read(os.path.join(PLAYLISTS_DIR, fname))
        if data and data.get("tracks"):
            yield data


# ─── публичное API ────────────────────────────────────────────

def save(name: str, tracks: list, author_id: int, author_name: str):
    """Сохраняет плейлист. Возвращает (ok, was_update)."""
    safe = _sanitize_name(name)
    if not safe:
        return False, False

    cleaned = []
    for t in tracks:
        if not t.get("title"):
            continue
        cleaned.append({
            "title": t["title"],
            "duration": int(t.get("duration", 0) or 0),
            "query": t.get("query") or t["title"],
        })
    if not cleaned:
        return False, False

    _ensure_dir()
    path = _path_for(safe)
    existed = os.path.exists(path)

    now = datetime.utcnow().isoformat()
    data = {
        "name": name.strip(),
        "safe_name": safe.lower(),
        "author_id": author_id,
        "author_name": author_name,
        "created_at": now,
        "updated_at": now,
        "tracks": cleaned,
    }

    # сохраняем created_at при перезаписи
    if existed:
        old = _read(path)
        if old and old.get("created_at"):
            data["created_at"] = old["created_at"]

    try:
        _atomic_write(path, data)
        return True, existed
    except Exception as e:
        print(f"⚠️ save playlist {safe}: {e}")
        return False, False


def load(name: str, author_id: int = None):
    """Загружает один плейлист по имени. author_id — если нужен чей-то конкретный."""
    safe = _sanitize_name(name).lower()
    if not safe:
        return None

    # точное совпадение имени файла
    path = _path_for(safe)
    if os.path.exists(path):
        data = _read(path)
        if data and (author_id is None or data.get("author_id") == author_id):
            return data

    # fallback — fuzzy по всем
    best, best_ratio = None, 0.0
    for data in _iter_all():
        n = data.get("name", "")
        if author_id is not None and data.get("author_id") != author_id:
            continue
        ratio = SequenceMatcher(None, safe, _sanitize_name(n).lower()).ratio()
        if ratio > best_ratio:
            best_ratio, best = ratio, data

    return best if best_ratio >= 0.7 else None


def search(query: str, limit: int = 10):
    """Поиск по подстроке/fuzzy. Пустой query — все.
    Возвращает (top-N, всего_найдено)."""
    q = (query or "").strip().lower()
    results = []

    for data in _iter_all():
        name = data.get("name", "")
        safe = _sanitize_name(name).lower()

        if not q:
            results.append(data)
            continue

        if q in safe:
            results.append(data)
            continue

        ratio = SequenceMatcher(None, q, safe).ratio()
        if ratio >= 0.65:
            results.append(data)

    results.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
    total = len(results)
    return results[:limit], total


def delete(name: str, author_id: int) -> bool:
    """Удаляет плейлист, если он принадлежит author_id."""
    safe = _sanitize_name(name).lower()
    path = _path_for(safe)
    if not os.path.exists(path):
        return False
    data = _read(path)
    if not data or data.get("author_id") != author_id:
        return False
    try:
        os.remove(path)
        return True
    except OSError:
        return False


def to_tracks(playlist: dict) -> list:
    """Плейлист → список треков для очереди."""
    return [
        {
            "title": t["title"],
            "duration": t.get("duration", 0),
            "query": t.get("query") or t["title"],
        }
        for t in playlist.get("tracks", [])
    ]
def rename(old_name: str, new_name: str, author_id: int) -> tuple[bool, str]:
    """Переименовывает плейлист. Возвращает (ok, error_message)."""
    safe_old = _sanitize_name(old_name).lower()
    safe_new = _sanitize_name(new_name).lower()

    if not safe_new:
        return False, "Недопустимое имя"

    old_path = _path_for(safe_old)
    new_path = _path_for(safe_new)

    if not os.path.exists(old_path):
        return False, "Плейлист не найден"

    if os.path.exists(new_path):
        return False, "Плейлист с таким именем уже существует"

    data = _read(old_path)
    if not data or data.get("author_id") != author_id:
        return False, "Нет прав"

    data["name"] = new_name.strip()
    data["safe_name"] = safe_new
    data["updated_at"] = datetime.utcnow().isoformat()

    try:
        _atomic_write(new_path, data)
        os.remove(old_path)
        return True, ""
    except Exception as e:
        return False, str(e)


def dedup(name: str, author_id: int) -> tuple[bool, int]:
    """Убирает дубликаты по title. Возвращает (ok, how_many_removed)."""
    safe = _sanitize_name(name).lower()
    path = _path_for(safe)

    if not os.path.exists(path):
        return False, 0

    data = _read(path)
    if not data or data.get("author_id") != author_id:
        return False, 0

    seen = set()
    unique = []
    for t in data.get("tracks", []):
        title = t.get("title", "").strip()
        if title and title not in seen:
            seen.add(title)
            unique.append(t)

    removed = len(data["tracks"]) - len(unique)
    if removed == 0:
        return True, 0

    data["tracks"] = unique
    data["updated_at"] = datetime.utcnow().isoformat()

    try:
        _atomic_write(path, data)
        return True, removed
    except Exception:
        return False, 0


def remove_track_at(name: str, author_id: int, index: int) -> tuple[bool, dict | None]:
    """Удаляет трек по индексу. Возвращает (ok, removed_track)."""
    safe = _sanitize_name(name).lower()
    path = _path_for(safe)

    if not os.path.exists(path):
        return False, None

    data = _read(path)
    if not data or data.get("author_id") != author_id:
        return False, None

    tracks = data.get("tracks", [])
    if index < 0 or index >= len(tracks):
        return False, None

    removed = tracks.pop(index)
    data["updated_at"] = datetime.utcnow().isoformat()

    try:
        _atomic_write(path, data)
        return True, removed
    except Exception:
        return False, None


def list_by_author(author_id: int) -> list[dict]:
    """Все плейлисты конкретного автора."""
    if not os.path.isdir(PLAYLISTS_DIR):
        return []
    result = []
    for fname in os.listdir(PLAYLISTS_DIR):
        if not fname.endswith(".json"):
            continue
        data = _read(os.path.join(PLAYLISTS_DIR, fname))
        if data and data.get("author_id") == author_id:
            result.append(data)
    result.sort(key=lambda d: d.get("updated_at", ""), reverse=True)
    return result
def shuffle(name: str, author_id: int) -> tuple[bool, int]:
    """Перемешивает треки. Возвращает (ok, total_tracks)."""
    safe = _sanitize_name(name).lower()
    path = _path_for(safe)

    if not os.path.exists(path):
        return False, 0

    data = _read(path)
    if not data or data.get("author_id") != author_id:
        return False, 0

    tracks = data.get("tracks", [])
    if len(tracks) < 2:
        return True, len(tracks)  # нечего перемешивать

    random.shuffle(tracks)
    data["tracks"] = tracks
    data["updated_at"] = datetime.utcnow().isoformat()

    try:
        _atomic_write(path, data)
        return True, len(tracks)
    except Exception as e:
        print(f"⚠️ shuffle {safe}: {e}")
        return False, 0


def move_track(name: str, author_id: int, index: int, direction: int) -> tuple[bool, int, str]:
    """Перемещает трек на direction позиций (-1 = вверх, +1 = вниз).
    Возвращает (ok, new_index, error_message)."""
    safe = _sanitize_name(name).lower()
    path = _path_for(safe)

    if not os.path.exists(path):
        return False, -1, "плейлист не найден"

    data = _read(path)
    if not data or data.get("author_id") != author_id:
        return False, -1, "нет прав"

    tracks = data.get("tracks", [])
    if index < 0 or index >= len(tracks):
        return False, -1, "неверный индекс"

    new_index = index + direction
    if new_index < 0 or new_index >= len(tracks):
        return False, index, "уже крайний"

    tracks[index], tracks[new_index] = tracks[new_index], tracks[index]
    data["updated_at"] = datetime.utcnow().isoformat()

    try:
        _atomic_write(path, data)
        return True, new_index, ""
    except Exception as e:
        print(f"⚠️ move {safe}: {e}")
        return False, -1, str(e)

def append(name: str, tracks: list, author_id: int, author_name: str, is_admin: bool = False):
    """Дополняет существующий плейлист (или создаёт, если нет).
    Возвращает (ok, created, total_tracks, added_count)."""
    safe = _sanitize_name(name)
    if not safe or not tracks:
        return False, False, 0, 0

    _ensure_dir()
    path = _path_for(safe)
    existed = os.path.exists(path)

    if existed:
        old = _read(path)
        if not old:
            return False, False, 0, 0

        # защита: чужой плейлист не трогаем (если не админ)
        if old.get("author_id") != author_id and not is_admin:
            return False, False, 0, 0

        # объединяем, убираем дубли по title
        existing_titles = {t["title"] for t in old.get("tracks", [])}
        new_tracks = []
        for t in tracks:
            if t.get("title") and t["title"] not in existing_titles:
                new_tracks.append({
                    "title": t["title"],
                    "duration": int(t.get("duration", 0) or 0),
                    "query": t.get("query") or t["title"],
                    "video_id": t.get("video_id"),
                })
                existing_titles.add(t["title"])

        if not new_tracks:
            # нечего добавлять (всё уже есть)
            return True, False, len(old.get("tracks", [])), 0

        old["tracks"].extend(new_tracks)
        old["updated_at"] = datetime.utcnow().isoformat()

        try:
            _atomic_write(path, old)
            return True, False, len(old["tracks"]), len(new_tracks)
        except Exception as e:
            print(f"⚠️ append playlist {safe}: {e}")
            return False, False, 0, 0

    # плейлиста нет — создаём через save
    ok, _ = save(name, tracks, author_id, author_name)
    return ok, True, len(tracks) if ok else 0, len(tracks) if ok else 0
