# job-pause

Freeze and unfreeze long-running jobs (renders, encodes, batch scripts) without losing their progress, for example to free the machine for an hour.

```bash
./job_pause.sh pause  'Blender -b' 'render_queue\.sh'
./job_pause.sh status 'Blender -b'
./job_pause.sh resume 'Blender -b' 'render_queue\.sh'
```

- Uses `kill -STOP` / `kill -CONT` only: nothing is ever killed.
- Patterns match the full command line. List a queue script before its workers, so the queue can't start a new job while paused.
- Patterns that are too loose (e.g. `node`) can freeze apps you're using. Be specific.
- macOS / Linux, zsh.
