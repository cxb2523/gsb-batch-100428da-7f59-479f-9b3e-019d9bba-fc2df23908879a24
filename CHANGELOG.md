# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

### Added

- Initial release with core features

### Page Fetch Outcomes

- Every page fetch now returns an explicit outcome (`success`, `timeout`, `invalidPage`,
  `emptySource`) together with the original `isValidPage` and `pageSource` fields.
- Search engine page and target page outcomes are counted separately and shown in the
  console status line (`Src OK`, `Src Timeout`, `Src Invalid`, `Src Empty`, `Src Last`).
- Development mode derives the outcome from the sample file name instead of a random value.
- Added local sample sources under `sources/page` and `sources/engine/<engine name>`,
  including an empty file to verify the empty source outcome.
- A link that fails with a timeout is written to the error links file only once.

