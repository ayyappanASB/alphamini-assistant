"""
actions.py
───────────
Alpha Mini action / dance / expression catalog for the UI buttons.
"""

CATEGORIES = [
    ("greetings",   "👋  Greetings"),
    ("dances",      "💃  Dances"),
    ("martial",     "🥋  Martial Arts & Sport"),
    ("fun",         "🎉  Fun & Cute"),
    ("calm",        "🧘  Calm & Poses"),
    ("expressions", "😊  Expressions (Eyes)"),
]


ACTIONS = {
    # ─── GREETINGS ───
    "wave":      {"category": "greetings", "label": "Wave",      "icon": "👋", "kind": "action", "name": "Surveillance_003", "tts": "Hello everyone!",     "kids": True},
    "hello":     {"category": "greetings", "label": "Hello",     "icon": "🤖", "kind": "action", "name": "say_hello_avatar", "tts": "",                    "kids": True},
    "bow":       {"category": "greetings", "label": "Bow",       "icon": "🙇", "kind": "action", "name": "bow_avatar",       "tts": "Thank you very much!","kids": True},
    "welcome":   {"category": "greetings", "label": "Welcome",   "icon": "🤝", "kind": "action", "name": "015",              "tts": "Welcome!",            "kids": True},
    "blow_kiss": {"category": "greetings", "label": "Blow Kiss", "icon": "💋", "kind": "action", "name": "Surveillance_004", "tts": "I love you!",         "kids": True},

    # ─── DANCES ───
    "dance_healthy": {"category": "dances", "label": "Healthy Song",  "icon": "🎵", "kind": "dance", "name": "dance_0001en", "tts": "",         "kids": True},
    "dance_shaolin": {"category": "dances", "label": "Shaolin Hero",  "icon": "🥋", "kind": "dance", "name": "dance_0002en", "tts": "",         "kids": True},
    "dance_apple":   {"category": "dances", "label": "Apple Dance",   "icon": "🍎", "kind": "dance", "name": "dance_0003en", "tts": "",         "kids": True},
    "dance_star":    {"category": "dances", "label": "Little Star",   "icon": "⭐", "kind": "dance", "name": "dance_0004en", "tts": "",         "kids": True},
    "dance_worm":    {"category": "dances", "label": "Greedy Worm",   "icon": "🐛", "kind": "dance", "name": "dance_0005en", "tts": "",         "kids": True},
    "dance_seaweed": {"category": "dances", "label": "Seaweed Dance", "icon": "🌊", "kind": "dance", "name": "dance_0006en", "tts": "",         "kids": True},
    "dance_abcd":    {"category": "dances", "label": "ABCD Dance",    "icon": "🔤", "kind": "dance", "name": "dance_0007en", "tts": "",         "kids": True},
    "dance_goodbye": {"category": "dances", "label": "Goodbye Dance", "icon": "👋", "kind": "dance", "name": "dance_0008en", "tts": "Goodbye!", "kids": True},
    "dance_meow":    {"category": "dances", "label": "Learn to Meow", "icon": "🐱", "kind": "dance", "name": "dance_0009en", "tts": "",         "kids": True},
    "dance_wave":    {"category": "dances", "label": "Wave Wave",     "icon": "🌀", "kind": "dance", "name": "dance_0010en", "tts": "",         "kids": True},
    "dance_dura":    {"category": "dances", "label": "Dura Dance",    "icon": "🕺", "kind": "dance", "name": "dance_0011en", "tts": "",         "kids": True},
    "dance_buddha":  {"category": "dances", "label": "Buddha Girl",   "icon": "🙏", "kind": "dance", "name": "dance_0012en", "tts": "",         "kids": True},
    "dance_pumpkin": {"category": "dances", "label": "Pumpkin",       "icon": "🎃", "kind": "dance", "name": "dance_0013",   "tts": "",         "kids": True},

    # ─── MARTIAL ARTS & SPORT ───
    "kungfu":         {"category": "martial", "label": "Kung Fu",       "icon": "🥋", "kind": "action", "name": "013", "tts": "Hi-ya!",              "kids": True},
    "pushups":        {"category": "martial", "label": "Push-ups",      "icon": "💪", "kind": "action", "name": "012", "tts": "One, two, three, four!","kids": True},
    "kick_right":     {"category": "martial", "label": "Right Kick",    "icon": "⚽", "kind": "action", "name": "018", "tts": "",                    "kids": True},
    "kick_left":      {"category": "martial", "label": "Left Kick",     "icon": "🦵", "kind": "action", "name": "019", "tts": "",                    "kids": True},
    "taichi":         {"category": "martial", "label": "Tai Chi",       "icon": "☯", "kind": "action", "name": "014", "tts": "",                    "kids": True},
    "bent_over":      {"category": "martial", "label": "Touch Toes",    "icon": "🤸", "kind": "action", "name": "011", "tts": "",                    "kids": True},
    "golden_rooster": {"category": "martial", "label": "One Leg Stand", "icon": "🦩", "kind": "action", "name": "016", "tts": "Look! Balance!",      "kids": True},
    "raise_hands":    {"category": "martial", "label": "Hands Up",      "icon": "🙌", "kind": "action", "name": "017", "tts": "",                    "kids": True},

    # ─── FUN & CUTE ───
    "laugh":        {"category": "fun", "label": "Laugh",       "icon": "😂", "kind": "action", "name": "010",              "tts": "Ha ha ha!",       "kids": True},
    "cute":         {"category": "fun", "label": "Selling Cute","icon": "🥰", "kind": "action", "name": "Surveillance_006", "tts": "Hee hee!",        "kids": True},
    "show_off":     {"category": "fun", "label": "Show Off",    "icon": "💁", "kind": "action", "name": "020",              "tts": "Look at me!",     "kids": True},
    "whatever":     {"category": "fun", "label": "Whatever",    "icon": "🤷", "kind": "action", "name": "021",              "tts": "",                "kids": True},
    "no_idea":      {"category": "fun", "label": "No Idea",     "icon": "❓", "kind": "action", "name": "022",              "tts": "Hmm, I don't know","kids": True},
    "celebrate":    {"category": "fun", "label": "Celebrate",   "icon": "🎉", "kind": "action", "name": "Surveillance_001", "tts": "Yay!",            "kids": True},
    "spin":         {"category": "fun", "label": "Spin",        "icon": "🌀", "kind": "action", "name": "Surveillance_002", "tts": "",                "kids": True},
    "dancing_pose": {"category": "fun", "label": "Dance Pose",  "icon": "💃", "kind": "action", "name": "Surveillance_005", "tts": "",                "kids": True},

    # ─── CALM & POSES ───
    "yoga":   {"category": "calm", "label": "Yoga",      "icon": "🧘", "kind": "action", "name": "024", "tts": "Calm and centered", "kids": True},
    "sit":    {"category": "calm", "label": "Sit Down",  "icon": "💺", "kind": "action", "name": "008", "tts": "",                  "kids": True},
    "stand":  {"category": "calm", "label": "Stand Up",  "icon": "🧍", "kind": "action", "name": "009", "tts": "",                  "kids": True},
    "buddha": {"category": "calm", "label": "Be Buddha", "icon": "🙏", "kind": "action", "name": "023", "tts": "Peace",             "kids": True},

    # ─── EXPRESSIONS ───
    "expr_exciting": {"category": "expressions", "label": "Excited",   "icon": "😄", "kind": "expression", "name": "codemao10", "tts": "", "kids": True},
    "expr_love":     {"category": "expressions", "label": "Love",      "icon": "💕", "kind": "expression", "name": "codemao19", "tts": "", "kids": True},
    "expr_surprise": {"category": "expressions", "label": "Surprised", "icon": "😲", "kind": "expression", "name": "codemao8",  "tts": "", "kids": True},
    "expr_laugh":    {"category": "expressions", "label": "Laughing",  "icon": "😂", "kind": "expression", "name": "codemao16", "tts": "", "kids": True},
    "expr_wink":     {"category": "expressions", "label": "Wink",      "icon": "😉", "kind": "expression", "name": "codemao21", "tts": "", "kids": True},
    "expr_blink":    {"category": "expressions", "label": "Blink",     "icon": "😌", "kind": "expression", "name": "codemao20", "tts": "", "kids": True},
    "expr_sleepy":   {"category": "expressions", "label": "Sleepy",    "icon": "😴", "kind": "expression", "name": "codemao2",  "tts": "", "kids": True},
    "expr_sad":      {"category": "expressions", "label": "Sad",       "icon": "😢", "kind": "expression", "name": "codemao4",  "tts": "", "kids": True},
    "expr_scared":   {"category": "expressions", "label": "Scared",    "icon": "😨", "kind": "expression", "name": "codemao7",  "tts": "", "kids": True},
    "expr_doubt":    {"category": "expressions", "label": "Doubt",     "icon": "🤔", "kind": "expression", "name": "codemao9",  "tts": "", "kids": True},
    "expr_fighting": {"category": "expressions", "label": "Fighting",  "icon": "💪", "kind": "expression", "name": "codemao11", "tts": "", "kids": True},
    "expr_hardwork": {"category": "expressions", "label": "Working",   "icon": "😤", "kind": "expression", "name": "codemao12", "tts": "", "kids": True},
    "expr_question": {"category": "expressions", "label": "Question",  "icon": "❓", "kind": "expression", "name": "codemao13", "tts": "", "kids": True},
    "expr_like":     {"category": "expressions", "label": "Like",      "icon": "👍", "kind": "expression", "name": "codemao14", "tts": "", "kids": True},
    "expr_anxious":  {"category": "expressions", "label": "Anxious",   "icon": "😰", "kind": "expression", "name": "codemao18", "tts": "", "kids": True},
}


def get_grouped_actions(kids_only=True):
    grouped = []
    for cat_id, cat_label in CATEGORIES:
        items = [(k, v) for k, v in ACTIONS.items()
                 if v.get("category") == cat_id and (not kids_only or v.get("kids", True))]
        if items:
            grouped.append((cat_id, cat_label, items))
    return grouped


def build_action_command(key, with_sound=True):
    spec = ACTIONS.get(key)
    if spec is None:
        return []

    kind = spec.get("kind", "action")
    if kind == "expression":
        return [{"type": "expression", "name": spec["name"]}]

    tts = spec.get("tts", "") if with_sound else ""
    if with_sound and tts:
        return [{"type": "action_with_sound", "name": spec["name"], "tts": tts, "wait": False}]
    return [{"type": "action", "name": spec["name"], "wait": False}]
