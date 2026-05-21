"""Clawdia Skill Library: persistent, searchable, self-improving skill artifacts.

Skills are markdown files organized as:
  /var/lib/clawdia/skills/<category>/<skill_id>.md

Each skill has:
  - title: human-readable name
  - trigger: regex pattern that matches the kind of task this solves
  - steps: markdown ordered list of steps
  - examples: prior successes (links to Telegram message IDs, dates)
  - created: ISO timestamp
  - last_refined: ISO timestamp
  - uses: integer count of times this skill was invoked
  - success_rate: float 0.0-1.0 (explicit feedback from Sean)

Skill creation and refinement is triggered by:
  1. Sean explicitly asks "save this as a skill" or "create a skill for..."
  2. Corrective feedback (if Sean says "no, do this instead") → auto-create
  3. Complex multi-step task completed → suggest creating a skill
"""
import os, json, re, hashlib, difflib
from datetime import datetime, timezone
from pathlib import Path

SKILLS_DIR = Path("/var/lib/clawdia/skills")
SKILL_CATEGORIES = ["personal", "work", "family", "clawdia", "music", "truck", "home", "finance", "general"]

def ensure_skills_dir():
    """Create skills directory if missing."""
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    for cat in SKILL_CATEGORIES:
        (SKILLS_DIR / cat).mkdir(exist_ok=True)

def skill_id_from_title(title: str) -> str:
    """Generate a stable skill ID from title. Example: 'Check calendar availability' -> 'check-calendar-availability'."""
    normalized = re.sub(r"[^a-z0-9]+", "-", title.lower().strip())
    return normalized.strip("-")

def load_skill(skill_id: str, category: str = "general") -> dict:
    """Load a skill by ID from category directory. Returns dict or None if not found."""
    skill_path = SKILLS_DIR / category / f"{skill_id}.md"
    if not skill_path.exists():
        return None
    
    content = skill_path.read_text()
    # Parse markdown front-matter (YAML-like header before first ##)
    match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not match:
        return None
    
    try:
        frontmatter = {}
        for line in match.group(1).strip().split("\n"):
            if ": " in line:
                k, v = line.split(": ", 1)
                frontmatter[k.strip()] = v.strip()
        frontmatter["id"] = skill_id
        frontmatter["category"] = category
        frontmatter["body"] = content[match.end():].strip()
        return frontmatter
    except:
        return None

def save_skill(skill_id: str, category: str, title: str, trigger: str, steps: str, 
               examples: str = "", success_rate: float = 0.5) -> bool:
    """Save or update a skill. steps and examples should be markdown-formatted."""
    if category not in SKILL_CATEGORIES:
        return False
    
    ensure_skills_dir()
    now = datetime.now(timezone.utc).isoformat()
    
    # Load existing to get created date and use count
    existing = load_skill(skill_id, category)
    created = existing.get("created", now) if existing else now
    uses = int(existing.get("uses", 0)) if existing else 0
    
    frontmatter = {
        "title": title,
        "trigger": trigger,
        "created": created,
        "last_refined": now,
        "uses": uses,
        "success_rate": success_rate,
    }
    
    # Build markdown
    fm_lines = ["---"]
    for k, v in frontmatter.items():
        if isinstance(v, str):
            v = v.replace("\"", "\\\"")
            fm_lines.append(f'{k}: "{v}"')
        else:
            fm_lines.append(f"{k}: {v}")
    fm_lines.append("---")
    
    body = f"""## Steps
{steps}

## Examples
{examples}
"""
    
    content = "\n".join(fm_lines) + "\n" + body
    skill_path = SKILLS_DIR / category / f"{skill_id}.md"
    skill_path.write_text(content)
    return True

def search_skills(query: str, category: str = "", limit: int = 10) -> list:
    """Search skills by title and trigger. Returns list of {id, title, trigger, category, uses, success_rate}."""
    ensure_skills_dir()
    results = []
    
    query_lower = query.lower()
    categories = [category] if category and category in SKILL_CATEGORIES else SKILL_CATEGORIES
    
    for cat in categories:
        cat_dir = SKILLS_DIR / cat
        if not cat_dir.exists():
            continue
        for skill_file in sorted(cat_dir.glob("*.md")):
            skill_id = skill_file.stem
            skill = load_skill(skill_id, cat)
            if not skill:
                continue
            
            # Search in title and trigger (rough match for now)
            title = skill.get("title", "").lower()
            trigger = skill.get("trigger", "").lower()
            
            # Score: exact title match > title substring > trigger match
            if title == query_lower:
                score = 100
            elif query_lower in title:
                score = 50
            elif query_lower in trigger:
                score = 25
            else:
                continue  # no match
            
            results.append({
                "id": skill_id,
                "title": skill.get("title"),
                "trigger": skill.get("trigger"),
                "category": cat,
                "uses": int(skill.get("uses", 0)),
                "success_rate": float(skill.get("success_rate", 0.5)),
                "score": score,
            })
    
    # Sort by score desc, then uses desc
    results.sort(key=lambda r: (-r["score"], -r["uses"]))
    return results[:limit]

def list_skills(category: str = "", limit: int = 50) -> list:
    """List all skills in a category (or all if empty), sorted by use count."""
    ensure_skills_dir()
    results = []
    
    categories = [category] if category and category in SKILL_CATEGORIES else SKILL_CATEGORIES
    
    for cat in categories:
        cat_dir = SKILLS_DIR / cat
        if not cat_dir.exists():
            continue
        for skill_file in sorted(cat_dir.glob("*.md")):
            skill_id = skill_file.stem
            skill = load_skill(skill_id, cat)
            if skill:
                results.append({
                    "id": skill_id,
                    "title": skill.get("title"),
                    "trigger": skill.get("trigger"),
                    "category": cat,
                    "uses": int(skill.get("uses", 0)),
                    "success_rate": float(skill.get("success_rate", 0.5)),
                })
    
    # Sort by uses desc
    results.sort(key=lambda r: -r["uses"])
    return results[:limit]

def increment_skill_uses(skill_id: str, category: str):
    """Increment the use count for a skill after it's invoked."""
    skill = load_skill(skill_id, category)
    if skill:
        skill["uses"] = str(int(skill.get("uses", 0)) + 1)
        save_skill(skill_id, category, skill["title"], skill["trigger"], 
                   skill["body"], success_rate=float(skill.get("success_rate", 0.5)))

# Quick test
if __name__ == "__main__":
    ensure_skills_dir()
    print("Skills dir ready:", SKILLS_DIR)
    
    # Create a sample skill
    save_skill(
        "check-availability",
        "general",
        "Check Calendar Availability",
        r"find.*free.*time|when.*available|check.*calendar",
        """1. Query Google Calendar for the requested date range
2. Exclude busy blocks (anything marked 'busy' or with duration >1h)
3. List available 1-hour slots sorted by earliest first
4. Return 5 top suggestions with confidence score""",
        examples="✓ 2026-05-15 11:00 - helped find meeting slot for VA trip\n✓ 2026-05-10 14:30 - coordinated family dinner time"
    )
    
    results = search_skills("check availability")
    print("Search results:", results)
    
    all_skills = list_skills()
    print(f"Total skills: {len(all_skills)}")
