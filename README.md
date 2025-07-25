Automatically watches Twitch streams and uses AI to make segments in real time and upload them to Youtube.

Clipped xQc for ~two weeks before getting banned for impersonation?? (not even related to the auto clipping, just because the account didn't make it clear it wasn't really xQc. I disagree with the ruling 🙁)

<img width="1919" height="946" alt="image" src="https://github.com/user-attachments/assets/05dc1561-72bc-4516-8981-0ab7c669a332" />


```mermaid
flowchart TD
  subgraph "Recording Loop"
    A["RecorderManager.start"] --> B["Streamlink (twitch.tv/<channel>)"]
    B -- "stdout MPEG-TS" --> C["FFmpeg TEST (segment_time=N)"]
    C -->|"valid stream"| D["FFmpeg RECORDING (writes %Y%m%d-%H%M%S.mp4)"]
    D --> E[".mp4 chunks in output_dir"]
    B -- "stderr monitor" --> M["monitor_streamlink_health()"]
    M -- "ad break detected" --> N["pause chat ingestion"]
  end

  subgraph "Chat Ingestion"
    B2["start_watching_chat()"] --> ChatBuffer["live chat messages"]
    ChatBuffer --> DumpOnClip["dump_chat() per clip"]
  end

  E --> F["watch_and_analyze_clips() loop"]

  subgraph "Clip Watcher"
    F --> G["detect new .mp4 file stabilized"]
    G --> H{"stream offline?"}
    H -- "no" --> I["wait 5 min stability"]
    H -- "yes" --> J["repair clip via FFmpeg -movflags faststart"]
    J --> K["Final clip ready"]
    I --> K
    K --> L1["analysis_thread → analyze.py"]
    K --> L2["chat_thread → chat_overlay.py"]
    L2 --> ProcessedClip["processed_recordings/<clip>.mp4 with chat"]
    L1 --> ResponsesTXT["responses_folder/<clip>.txt"]
    L1 --> AnalysisDone
    L2 --> ChatDone
    AnalysisDone & ChatDone --> M2["check_segments()"]
  end

  subgraph "Segment Creation"
    M2 --> N2["run_full_segment_creation()"]
    N2 --> O1["read_all_responses()"]
    N2 --> O2["ask_gemini()"]
    O2 --> P["parse_gemini_response() → segments[]"]
    P --> Q["filter_segments_with_latest_clips()"]
    Q --> R{"segments to create?"}
    R -- "no" --> EndWait["wait for more clips"]
    R -- "yes" --> S["create_segments() loop"]
  end

  subgraph "create_segments() per segment"
      S --> S1["create_temp_clip() for each clip"]
      S1 --> S2["combine_clips() via FFmpeg concat"]
      S2 --> S3["post_process: SegmentPostProcessor"]
      S3 --> Thumb["create_thumbnail()"]
      S3 --> Upload["upload_to_youtube() (optional)"]
      S3 --> SegmentDone["segment .mp4 saved in segments_dir"]
  end

  SegmentDone --> Cleanup["cleanup used clips & responses"]
  Cleanup --> Done["🎉 Pipeline complete (loops for new content)"]
```
