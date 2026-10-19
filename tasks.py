"""Task catalogue derived from the MuLearn General Enablement task sheet."""
from dataclasses import dataclass

@dataclass(frozen=True)
class Task:
    code: str; title: str; hashtags: tuple[str,...]; points: int; evidence: str; rules: str; high_risk: bool=False

TASKS=(
 Task("discord_guide","Discord Guide",("#ge-discord-guide",),100,"certificate image","Certificate recipient name must match Discord display name (case-insensitive)."),
 Task("qseverse_wallet","Connect Qseverse Wallet",("#genesis-connector",),100,"screenshot","MuID and account/profile details must be visible and match the registered profile.",True),
 Task("self_intro","Self Introduction",("#ge-self-intro",),100,"Discord message or screenshot","Introduction must be substantive, include a photo, and identify the member."),
 Task("find_better","Find Better",("#ge-find-better",),200,"8-slide presentation","Presentation must contain at least 8 slides and document a local product/service, observation, user satisfaction, problems and improvements.",True),
 Task("level_skip","Level Skip",("#skip-lvl",),0,"profile","Requires reviewer verification of the member profile.",True),
 Task("typing_126","Typing Challenge 126",("#ge-typing-challenge-126",),200,"progress screenshot","Profile visible in screenshot must match Discord profile and show lesson 126."),
 Task("typing_285","Typing Challenge 285",("#ge-typing-challenge-285",),200,"progress screenshot","Profile visible in screenshot must match Discord profile and show lesson 285."),
 Task("typing_460","Typing Challenge 460",("#ge-typing-challenge-460",),200,"progress screenshot","Profile visible in screenshot must match Discord profile and show lesson 460."),
 Task("typing_685","Typing Challenge 685",("#ge-typing-challenge-685",),200,"progress screenshot","Profile visible in screenshot must match Discord profile and show lesson 685."),
 Task("life_goals","Define Your Life Goals",("#ge-life-goals",),100,"public LinkedIn link","Public post is at least 150 words, is about life/vision/goals, author matches member, and mentions MuLearn Foundation."),
 Task("mars_trek","Measure Olympus Mons on Mars",("#ge-sp-marstrek",),100,"NASA Trek screenshot","Use the specialised Olympus Mons measurement verifier."),
 Task("coding_15","30 Days of Coding - 15",("#ge-30-days-coding15",),200,"HackerRank link or screenshot","Owner matches member and evidence shows the 30 Days of Coding badge/progress at day 15."),
 Task("coding_30","30 Days of Coding - 30",("#ge-30-days-coding30",),200,"HackerRank link or screenshot","Owner matches member and evidence shows the 30 Days of Coding badge/progress at day 30."),
 Task("aami_story","Aami's Story",("#ge-aami-story",),20,"YouTube comment screenshot","Comment must visibly include the member name and registered MuID."),
 Task("drug_free","Drug Free Kerala Pledge",("#drugfreekerala-pledge",),50,"official badge/certificate","OCR recipient name must match Discord display name."),
 Task("change_look","Change the Look",("#ge-change-the-look",),100,"Figma file link or owner screenshot","Frame must be named for member and Figma owner evidence must match member.",True),
 Task("snakify_6","Python through Snakify - 6",("#ge-snakify-6",),200,"progress screenshot","Screenshot must show logged-in member identity and at least 6 completed modules."),
 Task("snakify_11","Python through Snakify - 11",("#ge-snakify-11",),200,"progress screenshot","Screenshot must show logged-in member identity and at least 11 completed modules."),
 Task("failed_stories","Fail Forward",("#ge-failed-stories",),50,"public LinkedIn link","Public post is at least 250 words, about failure/learning, author matches member and mentions MuLearn Foundation."),
 Task("my_blog","Tell the World",("#ge-my-blog",),200,"public blog link","Blog is at least 400 words and starts with or clearly includes member name and registered MuID."),
 Task("speaking_listening","Effective Speaking and Listening",("#ge-speaking-listening",),250,"LinkedIn link and certificate","Post is at least 200 words, author matches member and mentions GTech MuLearn and Wadhwani Foundation."),
)

def task_for_content(content:str)->Task|None:
    text=content.casefold()
    return next((task for task in TASKS if any(tag.casefold() in text for tag in task.hashtags)),None)
