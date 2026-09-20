PRIZE_LADDER = [
    1_000, 2_000, 5_000, 10_000, 20_000,
    40_000, 80_000, 160_000, 320_000, 640_000,
    1_250_000, 2_500_000, 5_000_000, 7_500_000, 10_000_000,
]

SAFE_LEVELS = {10_000, 80_000, 1_000_000}

QUESTION_TIMERS = {
    1: 45, 2: 45, 3: 45, 4: 45, 5: 45,
    6: 45, 7: 45, 8: 45, 9: 45, 10: 45,
    11: 45, 12: 45, 13: 45,
    14: 45, 15: 45,
}

DIFFICULTY_BY_SEQUENCE = {
    1: "EASY", 2: "EASY", 3: "EASY", 4: "EASY",
    5: "MEDIUM", 6: "MEDIUM", 7: "MEDIUM",
    8: "HARD", 9: "HARD", 10: "HARD",
    11: "EXPERT", 12: "EXPERT", 13: "EXPERT",
    14: "VERY_HARD", 15: "VERY_HARD",
}

DEFAULT_LIFELINES = {"50_50": True, "FLIP": True}

FASTEST_FINGER_BONUS = [5_000, 3_000, 2_000]
FASTEST_FINGER_OTHERS = 1_000

FASTEST_PROMPT = {
    "text": "Arrange the following from earliest to latest:",
    "items": ["Internet", "Smartphone", "Moon Landing", "World Wide Web"],
    "options": [
        ["Moon Landing", "World Wide Web", "Internet", "Smartphone"],
        ["World Wide Web", "Moon Landing", "Internet", "Smartphone"],
        ["Moon Landing", "Internet", "World Wide Web", "Smartphone"],
        ["Internet", "World Wide Web", "Moon Landing", "Smartphone"],
    ],
    "correct_order": ["Moon Landing", "World Wide Web", "Internet", "Smartphone"],
}

FASTEST_PROMPTS = [
    FASTEST_PROMPT,
    {
        "text": "Arrange these inventions from earliest to latest:",
        "items": ["Telephone", "Television", "Computer", "Internet"],
        "correct_order": ["Telephone", "Television", "Computer", "Internet"],
    },
    {
        "text": "Arrange these Indian milestones from earliest to latest:",
        "items": ["Republic of India", "First Metro", "Chandrayaan-1", "UPI"],
        "correct_order": ["Republic of India", "First Metro", "Chandrayaan-1", "UPI"],
    },
]
