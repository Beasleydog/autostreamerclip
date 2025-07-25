combine_clips_to_segments_prompt = """You've been given a set of descriptions for a set of chunks from the streamer <STREAMER_NAME>. You must output segments that should be posted as longer-form youtube videos. 

Here are some examples of past segments. Use this as a guide for the style of the segments:
<EXAMPLES>

for each segment, list the mp4 file titles and the start time and end time for each file. each segment should be made up of multiple mp4 files.
the segments must be made up of multiple related clips.
each segment should have a main task, idea, or activity that the streamer is doing. 

WHEN CHOOSING TIMESTAMPS, IT IS CRUCIAL THAT YOU GET ANY RELATED YAP FROM THE STREAMER.
FOR EXAMPLE, IF WATCHING A VIDEO AND THE VIDEO ENDS AND THE STREAMER TALKS FOR 30 SECONDS, THE SEGMENT SHOULD END AT THE END OF THE STREAMER'S TALK NOT AT THE END OF THE VIDEO.

CRITICAL GROUPING RULES:
- DO NOT SPLIT THE SAME TYPE OF CONTENT INTO MULTIPLE SEGMENTS. If the streamer is doing the same general activity across multiple clips, they MUST be combined into ONE segment.
- "Watching TikToks", "Reacting to TikToks", "Viewing TikToks" are ALL THE SAME ACTIVITY - combine them into ONE segment called "Watching TikToks"
- "Playing [Game X]", "Continuing [Game X]", "[Game X] Gameplay", etc are ALL THE SAME ACTIVITY - combine them into ONE segment
- "Watching YouTube videos", "Reacting to YouTube", "YouTube reactions" are ALL THE SAME ACTIVITY - combine them into ONE segment
- Minor variations in wording do NOT justify separate segments (e.g., "funny" vs "viral" TikToks)
- Always consider ALL the content that you're given, go up to the end of the content if needed to continue the segment
- If the streamer sorta switches their focus but they're still in the SAME VIDEO GAME, it should be ONE SEGMENT
    - For example, the drawing portion of Gartic Phone and the reviewing portion of Gartic Phone should be THE SAME ONE SEGMENT. Every round should be COMBINED INTO ONE SEGMENT, do not overly split shit.
- NEVER EVER, UNDER ANY FUCKING CIRCUMSTANCES, SPLIT THE SAME YOUTUBE VIDEO INTO MULITPLE CHUNKS.
    - IF YOU CAN'T CLEARLY SEE THE TITLE, USE THE CONTEXT OF THE CONTENT TO DETERMINE IF IT'S THE SAME VIDEO.
- NEVER EVER EVER EVER CHOP THE START OR END OFF OF A YOUTUBE VIDEO. ONCE THE STREAM STARTS WATCHING A VIDEO, YOU MUST GET THE FULL THING UNTIL THEY HAVE VERY CLEARLY SWITCHED ACTIVITIES
SPECIAL CONTENT RULES:
- SHORT-FORM CONTENT (TikToks, YouTube Shorts, Instagram Reels, Twitter videos): Combine ALL into ONE segment regardless of platform or description variations
- LONG-FORM CONTENT (YouTube videos, Twitch VODs, documentaries): Each distinct video gets its OWN segment. Include the video title in the segment name (e.g., "Reacting to [Video Title]", "Watching [Video Title]"). 
    - Be sure to include any yap that the streamer does about the video after the video ends when making the timestamps
    - NEVER, EVER, FUCKING EVER, SPLIT UP A YOUTUBE VIDEO INTO MULTIPLE SEGMENTS. ANALYZE THE CONTENT AND WHAT IS HAPPENING IN THE VIDEO TO DETERMINE IF IT'S THE SAME VIDEO.
    - IF YOU SPLIT UP A YOUTUBE VIDEO INTO MULTIPLE SEGMENTS, YOU WILL BE FIRED. PEOPLE WILL DIE. THE EARTH WILL CRUMBLE.
    - NEVER FUCKING SPLIT UP A YOUTUBE VIDEO INTO MULTIPLE SEGMENTS.

DO NOT COMBINE CLIPS THAT ARE TRULY DIFFERENT ACTIVITIES:
- Gaming vs TikTok watching = different segments
- Chatting vs Gaming = different segments  
- Different games = different segments (unless they're very similar, like different Mario Kart versions)
    - Note that if the streamer is playing one game but doing multiple activities within that game, it should be ONE SEGMENT
- Different YouTube videos = different segments (each video gets its own)

SEGMENT NAME RULES:
- The segment name MUST include the streamer's name in the title.
- If the streamer is playing with other people, include the other people's names in the segment name if they are famous and recognizable
- The segment name should be a single phrase that describes the SINGLE main activity or task the streamer is doing.
- It MUST be a youtube title style phrase, a bit sensational/clickbaity but STILL ACCURATE
- IT SHOULD HAVE ONE MAIN DETAIL, DEFINITELY NEVER "X AND Y"
- SIMPLE IS BETTER
- The segment name should be concise and to the point.
- The segment name ABSOLUTELY MUST be in the style of the past examples.
- The segment name should be no more than 100 characters.
- The segment name should be no more than 10 words.
- The segment name should be no more than 100 characters.

SPECIAL RULES:
- If there is content that should be segmented but there's a bit of semi-unrelated content in between, include it as one whole segment - it will be handled later
- Don't worry about distinguishing different versions of games - combine them (e.g., Mario Kart Tour + Mario Kart World = "Playing Mario Kart")

Each segment MUST be at least 5 minutes long. If it's not this long, skip it.

IMPORTANT TIMESTAMP FORMAT RULES:
- All START and END timestamps **MUST** be provided in **MM:SS** format.
- Do **NOT** output raw seconds like "600" nor decimal values like "600.8".
- Use a two-digit field for hours/minutes/seconds where applicable.

MAKE SURE NOT TO END THE SEGMENT TOO EARLY, WE NEED ALL THE RELATED CONTENT.

NOTE THAT IF THERE ISN'T REALLY ANY CLEAR SEGMENTS THAT ARE WORTH DOING (LIKE IF THE STREAMER IS JUST TALKING ABOUT RANDOM STUFF), THEN JUST EXPLAIN THAT IN YOUR OUTPUT. SAY THAT THERES NOTHING TO BE SEGMENTED.

Your output must follow this format:
START SEGMENT
SEGMENT_NAME:the name here, make it follow the style of the past videos
START CLIPS
CLIP_FILE:the exact mp4 file name
START:the start time for this file
END:the end time for this file
repeat for all the clips included in this segment
END CLIPS
END SEGMENT"""

def build_combine_clips_to_segments_prompt(streamer_name: str, examples: list[str]):
    return combine_clips_to_segments_prompt.replace("<STREAMER_NAME>", streamer_name).replace("<EXAMPLES>", "\n".join(examples))