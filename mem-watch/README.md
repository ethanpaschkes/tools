# mem-watch

Watches a Mac's memory while a long job runs, summed per application (a browser is dozens of processes). Warns past a limit and never kills anything.

```bash
node mem-watch.mjs                 # watch, warn at 5 GB per app, sample every 5 s
node mem-watch.mjs --limit 3 --every 2 --log mem.log
node mem-watch.mjs --once          # one reading
```

It can print a reversible `kill -STOP <pid>` suggestion for you to run yourself, and refuses to suggest it for system processes like WindowServer. Needs Node 18+.
