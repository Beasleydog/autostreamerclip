analyze_single_clip_prompt = """This is a clip from the streamer <STREAMER_NAME>.
Return a list of his main, high level activity(s) during this clip.
Here are some examples of acceptable labels so you can understand the high level nature of the labels:
<EXAMPLES>

Note that a clip can have multiple labels, as long as the streamer is truly doing multiple things long term rather than just momentarily switching. 
these labels must be COMPLETELY different types of content. 
for example, switching from reacting to a youtube video to playing a game, or switching from minecraft to fortnite. if the main theme/topic is the same then it should just be one label. 

THIS RULE IS ABSOLUTELY CRITICAL:
each label must be for a chunk of time that is at LEAST 1:30 minutes. if it is not long enough, then it should not be its own label and instead be combined with nearby similar activities.
IF EVEN A SINGLE LABEL IS SHORTER THAN 1:30 MINUTES THEN IT SHOULD NOT BE ITS OWN LABEL AND SHOULD BE COMBINED WITH NEARBY SIMILAR ACTIVITIES.
IF YOU GIVE A LABEL SHORTER THAN 1:30 MINUTES, YOU WILL BE FIRED AND THE WORLD WILL EXPLODE.

for each label, give ONE timestamp for the start and ONE for end.
note that its ok if the label contains some tangential activity from the streamer in the middle, as long as the label is accurate for the main chnk.

If the streamer is reacting to a piece of content that is not overly shortform like tiktok, include the name or description of the content.
If the streamer is playing a game include the name of the game. 
Use similar logic for other labels.

If you see gameplay, be sure to doublecheck whether the streamer is PLAYING the game or just watching it. Listening to what they say will help you make this determination.
If you see a long form youtube video, BE SURE TO MENTION THE EXACT TIMESTAMP THAT THE STREAMER STARTS WATCHING OR FINISHES WATCHING THE VIDEO.
    - Note that the streamer may be watching the video for the entire clip, obviously do NOT say anything about starting or stopping watching the video if they are watching the video for the entire clip.

be EXTERMELY PRECISE with your timestamps. every timestamp MUST be accurate to the second for when the streamer starts and stops doing the activity. check your work here.

Once you have your labels, before you actually return them, check your work. if ANY SINGLE LABEL IS SHORTER THAN 1:30 MINUTES, then you MUST COMBINE IT WITH NEARBY SIMILAR ACTIVITIES.
If you do not do this, you will be fired and the world will explode.

your output format should be
LABEL:the name here
START_TIME:the start time of the label
END_TIME:the end time of the label

for example:
LABEL:watching tiktoks
START_TIME:00:00:00
END_TIME:00:01:00

If you have multiple labels, you should have multiple lines like this:
LABEL:watching tiktoks
START_TIME:00:00:00
END_TIME:00:01:00

LABEL:watching the youtube video "Are we cooked"
START_TIME:00:01:00
END_TIME:00:02:00
"""

def build_analyze_single_clip_prompt(streamer_name: str, examples: list[str]):
    return analyze_single_clip_prompt.replace("<STREAMER_NAME>", streamer_name).replace("<EXAMPLES>", "\n".join(examples))