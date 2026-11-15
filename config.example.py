# Welche Quellen gescrapt werden sollen: "freelancermap", "arbeitsamt"
SOURCES = ["freelancermap", "arbeitsamt"]

MAX_PAGES = 10  # freelancermap: wie viele Ergebnisseiten pro Lauf
MIN_SCORE = 30  # Treffer unter diesem Score werden verworfen, nicht gespeichert

PROFILE = {
    "skills": [
        "Python",
        "JavaScript",
        "React",
        "Vue",
        "AWS",
    ],
    "preferred_keywords": [
        "Backend",
        "Frontend",
        "Fullstack",
    ],
    "excluded_keywords": [
        "SAP",
        "Drupal",
    ],
}
