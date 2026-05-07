# video-flow

A small BiliNote workflow for turning Douyin and Xiaohongshu links into structured notes.

## What it does

- reads links from a daily queue file
- submits them to BiliNote
- waits for the summary task to finish
- writes inbox and source notes
- keeps screenshot assets alongside the notes
- tracks processed items in a local manifest

## Layout

- `scripts/bilinote_workflow.py` - main workflow
- `queue/bilinote_daily_links.md` - daily input queue
- `state/` - local run state and manifest files
- `archive/legacy_collector/` - older collector workflow kept for reference

## Quick start

1. Put one video link per line into `queue/bilinote_daily_links.md`.
2. Run:

```powershell
python .\scripts\bilinote_workflow.py run
```

3. The workflow will process any new links and update the local notes.

## Configuration

Set these environment variables if your local paths differ:

- `BILINOTE_WORKSPACE`
- `BILINOTE_ROOT`
- `BILINOTE_VAULT_ROOT`
- `BILINOTE_STATE_ROOT`
- `BILINOTE_DAILY_LINKS`

## Notes

- cleanup only reports candidates; it does not delete files
- the legacy collector is archived, not the main entry point
