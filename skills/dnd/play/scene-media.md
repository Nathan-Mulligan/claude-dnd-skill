# Scene, music and images

Read once when the party reaches its first new place this session.

- When the party reaches a new place, or a place's mood truly changes, add `--scene "<place>: <what is happening>"` to that narration's `send.py` call. After the colon, describe the actual view and its atmosphere, including darkness where appropriate: it drives the music and the background image.
- Add `--mood calm|curious|ominous|tense|warm` only when the moment's feel differs from the place.
- No `/health` read is needed. Repeating the current place changes nothing, so don't re-send a scene to be safe. `send.py` prints one line saying what it chose.
- Fights: `--music-event combat-start` on the first combat narration, `--music-event combat-end` on the first narration after it.
- Never send a probe or debug line to test the display; it goes into the players' story.
- Image generation is separate and off unless the campaign says otherwise.
