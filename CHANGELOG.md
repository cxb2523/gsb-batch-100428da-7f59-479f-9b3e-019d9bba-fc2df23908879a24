## 1.1.0

* (01) Not check userId in cookies
* (02) Supports downloading quizzes
* (03) Supports selecting subtitle languages with `--subtitles`
* (04) Generates a `playlist.m3u` file for each course
* (05) Signs Windows executables with a self-signed certificate generated during the Nuitka build
* (06) Modernizes packaging with a PEP 621 `pyproject.toml`, trusted-publishing workflow, and removes legacy requirements files
* (07) A quiz whose questions cannot be fetched (failed attempt start, abnormal state, or empty draft) now gets a placeholder entry explaining why instead of disappearing from the course; it carries no download tasks and is replaced by the real questions on a later successful re-crawl

## 1.0.1

* (01) Simplifies the user interface
* (02) Adds a troubleshooting guide
* (03) Adds Github CI
* (04) Generate Nuitka binaries

## 0.1.2

* (01) Removes `--email` and `--password`
* (02) Supports downloading "references" of a course
* (03) Checks whether there is a newer version
* (04) Provides SOME helpful feedbacks to users in SOME cases when dl_coursera is not used in the right way

## 0.1.1

* (01) Adds `--cookies`

## 0.1.0
