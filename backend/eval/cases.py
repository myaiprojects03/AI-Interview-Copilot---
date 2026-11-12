"""Test question set for prompt tuning (test CV: eval/test_cv.txt, test JD: eval/test_jd.txt)."""

# (interviewer turn as transcribed, should a question be detected?, words the detected question must contain)
DETECTION_CASES = [
    ("Go ahead and get started with a quick introduction. Could you tell me a little bit about yourself? "
     "Yeah, absolutely. First of all, thank you so much for having me.", True, ["yourself"]),
    ("So why exactly are you interested in interviewing with our consulting firm? Yeah, my reasons are twofold.",
     True, ["interested"]),
    ("So could you tell me about a time where you solved a difficult problem?", True, ["difficult problem"]),
    ("Okay, so could you dive a little bit more into that first one, your ability to persuade people? Maybe tell "
     "me about a time you were able to convince someone to change their mind.", True, ["convince"]),
    ("Walk me through how you would structure a cost review for a manufacturing client", True, ["cost review"]),
    ("Thanks so much for joining us today, it's great to meet you.", False, []),
    ("Can you hear me okay? Great.", False, []),
    ("Oh wow, that sounds like a very impressive background.", False, []),
    ("So could you dive a little bit more into that first one, your ability to", False, []),  # cut off mid-question
    ("And what would you say is your biggest weakness", True, ["weakness"]),
    ("How do you prioritise when two stakeholders want different things?", True, ["stakeholders"]),
    ("Okay awesome, that's good to know. Let's move on.", False, []),
]

# kind: story (expects STAR), motivation (expects bullets, no STAR), technical, other
# facts: at least one of these CV terms should appear (shows the answer is grounded in this CV)
ANSWER_CASES = [
    ("Could you tell me a little bit about yourself?", "behavioral", "motivation",
     ["Northwind", "business analyst", "industrial engineer"]),
    ("Why are you interested in joining Brightwater?", "behavioral", "motivation",
     ["energy", "education", "consulting"]),
    ("What are your key strengths?", "behavioral", "motivation",
     ["supplier", "KPI", "persuad", "analys", "Consulting Club"]),
    ("Tell me about a time you solved a difficult problem.", "behavioral", "story",
     ["supplier", "40", "12%", "120"]),
    ("Tell me about a time you convinced someone to change their mind.", "behavioral", "story",
     ["$2M", "plant director", "55%"]),
    ("Give me an example of when you showed leadership.", "behavioral", "story",
     ["Consulting Club", "30 to 85", "pro-bono"]),
    ("Describe a situation where you resolved a conflict between colleagues.", "behavioral", "story",
     ["project leads", "joint session", "engineering and finance"]),
    ("Tell me about a time you failed.", "behavioral", "story", []),  # not in CV: expects [placeholders]
    ("Tell me about a mistake you made at work and what you learned.", "behavioral", "story", []),
    ("How would you structure a cost review for a manufacturing client?", "technical", "technical",
     ["supplier", "scoring model", "cost"]),
    ("Which tools would you use to analyse a large operations dataset?", "technical", "technical",
     ["SQL", "Python", "Power BI", "Excel"]),
    ("What are your salary expectations?", "other", "salary", []),
    ("Where do you see yourself in five years?", "behavioral", "motivation", ["energy", "education", "consulting"]),
    ("What is your biggest weakness?", "behavioral", "weakness", []),
]

CV_ENTITIES = ["Northwind", "Halden", "Clearpoint", "Consulting Club", "Riverside", "supplier", "KPI", "$2M"]
