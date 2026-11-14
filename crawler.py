import gc
import heapq
import logging
import os
import pickle
from collections.abc import Mapping
from dataclasses import dataclass
from multiprocessing import Pool

from tqdm import tqdm

from corpus_interface import Document, DocumentAnnotation, UnifiedGetter
from document_rater import DocumentRater
from wandb_logger import WandbLogger

logger = logging.getLogger(__name__)


@dataclass
class ResumeState:
    """Verifiable starting point returned by ``Crawler.init_or_resume_state``.

    Attributes:
        iter_num: iteration from which the crawl should continue.
        num_selected_docs: cumulative number of docs already selected.
        queue_size: actual number of docs in the restored (and possibly
            recomputed) queue.
        dropped_docs: docs dropped while recomputing scores because their
            content could not be fetched; 0 when no recomputation happened.
        raters_recomputed: whether queue scores were recomputed because the
            rater set in the state file did not match the current raters.
    """

    iter_num: int
    num_selected_docs: int
    queue_size: int
    dropped_docs: int = 0
    raters_recomputed: bool = False

    def __iter__(self):
        # Backwards-compatible unpacking: ``iter_num, num_selected_docs = ...``
        yield self.iter_num
        yield self.num_selected_docs


class Crawler:
    def __init__(
        self,
        unified_getter: UnifiedGetter,
        quality_raters: DocumentRater | list[DocumentRater],
        output_dir: str | None = None,
        num_workers: int = 1,
        wandb_logger: WandbLogger = None,
        max_num_in_mem_docs: int = 1000000,
    ) -> None:
        self.unified_getter = unified_getter
        self.output_dir = output_dir
        self.queue = []
        self.visited = set()
        self.quality_raters = (
            quality_raters if isinstance(quality_raters, list) else [quality_raters]
        )
        self.num_workers = num_workers
        self.wandb_logger = wandb_logger
        self.max_num_in_mem_docs = max_num_in_mem_docs
        self.require_doc_content = any(rater.require_doc_text() for rater in self.quality_raters)

    def put_into_queue(self, documents: list[Document]) -> None:
        for document in documents:
            heapq.heappush(self.queue, (document.annotations, document.docid))
            self.visited.add(document.docid)
        logger.info(f"Size after put: {len(self.queue)}")

    @staticmethod
    def _get_mean_score_for_logging(
        annotations: list[DocumentAnnotation], postfix: str
    ) -> dict[str, float]:
        result_dict = {}
        for key in annotations[0].keys():
            result_dict[f"mean_{key}_{postfix}"] = sum(
                annotation[key] for annotation in annotations
            ) / len(annotations)
        return result_dict

    def _log_all(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if isinstance(value, float):
                logger.info(f"{key} = {value:.2f}")
            else:
                logger.info(f"{key} = {value}")
        if self.wandb_logger:
            self.wandb_logger.log(**kwargs)

    def pop_from_queue(self, num_docs: int) -> list[str]:
        annotations, docids = [], []
        for _ in range(num_docs):
            try:
                annotation, docid = heapq.heappop(self.queue)
                annotations.append(annotation)
                docids.append(docid)
            except IndexError:
                break
        mean_results = self._get_mean_score_for_logging(annotations, "pop")
        size_after_pop = len(self.queue)
        self._log_all(**mean_results, size_after_pop=size_after_pop)
        return docids

    def find_outinks(
        self, docids: list[str], with_predecessor_info: bool = False
    ) -> list[str] | list[tuple[str, list[str]]]:
        input_size = len(docids)
        with Pool(self.num_workers) as pool:
            results = list(
                tqdm(
                    pool.imap(self.unified_getter.get_outlinks, docids),
                    total=input_size,
                    desc="Finding outlinks",
                )
            )

        outlinks = []
        for result in results:
            outlinks.extend(result)
        if with_predecessor_info:
            outlinks_with_predecessor = []
            for docid, result in zip(docids, results, strict=True):
                outlinks_with_predecessor.append((docid, result))
        total_outlinks = len(outlinks)
        outlinks = set(outlinks)
        total_unique_outlinks = len(outlinks)
        outlinks -= self.visited
        total_unique_outlinks_unvisited = len(outlinks)
        self._log_all(
            total_outlinks=total_outlinks,
            total_unique_outlinks=total_unique_outlinks,
            total_unique_outlinks_unvisited=total_unique_outlinks_unvisited,
            expansion_ratio=total_outlinks / input_size,
            expansion_ratio_unique=total_unique_outlinks / input_size,
            expansion_ratio_unique_unvisited=total_unique_outlinks_unvisited / input_size,
            unvisited_ratio=total_unique_outlinks_unvisited / total_unique_outlinks,
        )
        outlinks = list(outlinks)
        return (outlinks, outlinks_with_predecessor) if with_predecessor_info else outlinks

    def _get_scores_for_docs(
        self, docids: list[str], current_partition: int, total_partitions: int
    ) -> list[Document]:
        if self.require_doc_content:
            # Fetch docs
            with Pool(self.num_workers) as pool:
                all_docs = list(
                    tqdm(
                        pool.imap(self.unified_getter.get_doc, docids),
                        total=len(docids),
                        desc=f"Getting docs (partition {current_partition}/{total_partitions})",
                    )
                )
            all_docs = [doc for doc in all_docs if doc is not None]
        else:
            all_docs = [Document(docid=docid) for docid in docids]

        results = all_docs
        for quality_rater in self.quality_raters:
            results = quality_rater(results)
        if self.require_doc_content:
            for document in results:
                del document.text
        return results

    def get_scores_for_docs(self, docids: list[str]) -> list[Document]:
        logger.info(f"Getting scores for {len(docids)} docs")
        docids_partitions = [
            docids[i : i + self.max_num_in_mem_docs]
            for i in range(0, len(docids), self.max_num_in_mem_docs)
        ]
        for i, docids_partition in enumerate(docids_partitions):
            logger.info(f"Partition {i+1}: {len(docids_partition)} docs")
        results: list[Document] = []
        for i, docids_partition in enumerate(docids_partitions):
            results.extend(
                self._get_scores_for_docs(
                    docids_partition,
                    current_partition=i + 1,
                    total_partitions=len(docids_partitions),
                )
            )
        if self.require_doc_content:
            doc_hit_rate = len(results) / len(docids)
            self._log_all(doc_hit_rate=doc_hit_rate)
        annotations = [x.annotations for x in results]
        mean_results = self._get_mean_score_for_logging(annotations, "push")
        self._log_all(**mean_results)
        return results

    def write_output(self, iter_num: int, docids: list[str]) -> None:
        with open(os.path.join(self.output_dir, f"iter_{iter_num}.docids.txt"), "w") as fout:
            for docid in docids:
                fout.write(f"{docid}\n")

    def save_state(self, iter_num: int, num_selected_docs: int) -> None:
        # ``iter_num`` is stored inside the state so resume no longer has to
        # infer it from the file name. The ``state_<n:06d>.pkl`` naming is
        # kept unchanged and old state files without this key remain readable
        # (the iteration falls back to being parsed from the file name).
        state = {
            "queue": self.queue,
            "visited": self.visited,
            "num_selected_docs": num_selected_docs,
            "iter_num": iter_num,
        }
        with open(os.path.join(self.output_dir, f"state_{iter_num:06d}.pkl"), "wb") as fout:
            pickle.dump(state, fout)

    @staticmethod
    def _iter_num_from_state_path(state_file: str) -> int:
        """Parse the iteration number from a ``state_<n>.pkl`` file name."""
        basename = os.path.basename(state_file)
        error_msg = (
            f"Cannot infer iteration number from state file name '{state_file}': "
            "expected a name of the form state_<n>.pkl (e.g. state_000042.pkl). "
            "Re-save the state with Crawler.save_state or pass a correctly named file."
        )
        parts = basename.split("_")
        if len(parts) < 2:
            raise ValueError(error_msg)
        num_part = parts[-1].split(".")[0]
        if not num_part.isdigit():
            raise ValueError(error_msg)
        return int(num_part)

    @staticmethod
    def _infer_rater_names(queue: list) -> set[str]:
        """Infer the set of annotation (scoring) keys from queue entries.

        Every queue entry is an ``(annotations, docid)`` tuple whose
        annotations expose the scoring keys of the raters used when the entry
        was scored (normalized rater keys are included alongside their base
        key). All entries must carry the same key set, otherwise the rater set
        cannot be determined unambiguously.
        """
        if not isinstance(queue, list) or not queue:
            raise ValueError("Cannot infer quality raters from an empty queue.")
        first_annotations = queue[0][0]
        if not isinstance(first_annotations, Mapping) or not first_annotations:
            raise ValueError(
                "Cannot infer quality rater set from the queue: the first entry "
                f"has no scoring annotations: {queue[0]!r}."
            )
        rater_names = set(first_annotations.keys())
        for index, entry in enumerate(queue[1:], start=1):
            try:
                annotations = entry[0]
            except (TypeError, IndexError):
                raise ValueError(
                    f"Malformed queue entry at index {index}: expected an "
                    f"(annotations, docid) tuple, got {entry!r}."
                )
            if not isinstance(annotations, Mapping) or not annotations:
                raise ValueError(
                    f"Cannot infer quality rater set: queue entry at index {index} "
                    f"has no scoring annotations: {entry!r}."
                )
            if set(annotations.keys()) != rater_names:
                raise ValueError(
                    "Queue entries carry inconsistent scoring keys, so the quality "
                    f"rater set cannot be inferred unambiguously. First entry keys: "
                    f"{sorted(rater_names)}; entry {index} keys: "
                    f"{sorted(annotations.keys())}."
                )
        return rater_names

    @staticmethod
    def _base_rater_names(annotation_keys: set[str]) -> set[str]:
        """Strip normalizer-derived keys from an annotation key set.

        Each rater writes one base key (its ``get_name()``) plus, when a
        normalizer is configured, a derived ``<name>_<normalizer>_normalized``
        key. Rater identity is therefore the set of keys that do not end in
        ``_normalized``; comparing base keys avoids recomputing merely because
        a normalizer produced extra annotation keys.
        """
        return {key for key in annotation_keys if not key.endswith("_normalized")}

    def _verify_resume_against_outputs(self, iter_num: int, num_selected_docs: int) -> None:
        """Cross-check the resumed counters against the iter_*.docids.txt files.

        This only reads already-written output files and never raises: a
        mismatch means the counters are not trustworthy and is surfaced as a
        warning rather than silently ignored.
        """
        if not self.output_dir:
            return
        output_files = [
            name
            for name in os.listdir(self.output_dir)
            if name.startswith("iter_") and name.endswith(".docids.txt")
        ]
        if not output_files:
            logger.warning(
                "Resuming with no iter_<n>.docids.txt output files found in "
                f"'{self.output_dir}'; the resumed counters cannot be cross-checked "
                "against written outputs."
            )
            return
        num_lines = 0
        num_files = 0
        for name in output_files:
            path = os.path.join(self.output_dir, name)
            try:
                with open(path, "r") as fin:
                    num_lines += sum(1 for line in fin if line.strip())
                num_files += 1
            except OSError as error:
                logger.warning(f"Could not read output file '{path}' for verification: {error}")
        expected_path = os.path.join(self.output_dir, f"iter_{iter_num - 1}.docids.txt")
        if not os.path.exists(expected_path):
            logger.warning(
                f"Resume expects the next iteration to be {iter_num}, but the "
                f"preceding output file '{expected_path}' does not exist."
            )
        if num_lines != num_selected_docs:
            logger.warning(
                f"Resumed num_selected_docs={num_selected_docs} does not match the "
                f"{num_lines} docids found across {num_files} output file(s) in "
                f"'{self.output_dir}'."
            )
        else:
            logger.info(
                f"Verified resumed counters against {num_files} output file(s): "
                f"{num_lines} selected docids match num_selected_docs."
            )

    def init_or_resume_state(self, state_file: str | None) -> ResumeState:
        if state_file is None:
            logger.info("Starting from scratch")
            return ResumeState(
                iter_num=0, num_selected_docs=0, queue_size=0, dropped_docs=0
            )
        logger.info(f"Resuming from state file: {state_file}")
        with open(state_file, "rb") as fin:
            state = pickle.load(fin)
        if not isinstance(state, Mapping) or "queue" not in state or "visited" not in state:
            raise ValueError(
                f"State file '{state_file}' is malformed: expected a mapping with "
                "'queue' and 'visited' entries."
            )
        if "num_selected_docs" not in state:
            raise ValueError(
                f"State file '{state_file}' is missing the 'num_selected_docs' entry."
            )

        self.queue, self.visited = state["queue"], state["visited"]
        num_selected_docs = state["num_selected_docs"]

        # Prefer the iteration stored inside the state; fall back to the file
        # name for legacy state files saved before this key existed.
        if "iter_num" in state:
            iter_num = state["iter_num"]
        else:
            logger.info(
                "State file does not store an iteration number (legacy format); "
                "inferring it from the file name."
            )
            iter_num = self._iter_num_from_state_path(state_file)

        if not isinstance(self.queue, list) or len(self.queue) == 0:
            raise ValueError(
                f"Cannot resume from '{state_file}': the queue in the state file is "
                "empty, so there is no document to continue crawling from."
            )
        if not isinstance(self.visited, set):
            raise ValueError(
                f"Cannot resume from '{state_file}': 'visited' must be a set, got "
                f"{type(self.visited).__name__}."
            )

        original_annotation_keys = self._infer_rater_names(self.queue)
        original_quality_raters = self._base_rater_names(original_annotation_keys)
        current_quality_raters = set(
            quality_rater.get_name() for quality_rater in self.quality_raters
        )
        gc.collect()

        raters_recomputed = False
        dropped_docs = 0
        if original_quality_raters != current_quality_raters:
            raters_recomputed = True
            logger.info("Quality raters mismatch")
            logger.info(
                f"Quality raters in state file: {sorted(original_quality_raters)}, "
                f"current quality raters: {sorted(current_quality_raters)}"
            )
            logger.info("Current first item in the queue:")
            logger.info(self.queue[0])
            logger.info("Recomputing scores for all docs in the queue")
            docids_to_score = [docid for _, docid in self.queue]
            recomputed = self.get_scores_for_docs(docids_to_score)
            # Content fetching may fail for some docs, which get_scores_for_docs
            # silently drops. Track how many disappeared instead of asserting
            # equality, and rebuild the queue only from docs that survived.
            recomputed_by_docid = {doc.docid: doc for doc in recomputed}
            dropped_docids = [
                docid for docid in docids_to_score if docid not in recomputed_by_docid
            ]
            dropped_docs = len(dropped_docids)
            if dropped_docs:
                logger.warning(
                    f"{dropped_docs} of {len(docids_to_score)} queued docs could not "
                    "be fetched during score recomputation and were dropped from the "
                    f"queue: {dropped_docids[:10]}{' ...' if dropped_docs > 10 else ''}"
                )
            if not recomputed:
                raise ValueError(
                    "Score recomputation dropped every document in the queue (no doc "
                    "content could be fetched); refusing to resume with an empty queue."
                )
            logger.info("Constructing new queue")
            new_queue = []
            for docid in docids_to_score:
                doc = recomputed_by_docid.get(docid)
                if doc is None:
                    continue  # Dropped: content unavailable during recomputation.
                new_queue.append((doc.annotations, docid))
            self.queue = new_queue
            heapq.heapify(self.queue)
            logger.info("After recomputation, first item in the queue:")
            logger.info(self.queue[0])
            # Recompute guarantees every surviving doc carries current rater keys.
            final_base_keys = self._base_rater_names(set(self.queue[0][0].keys()))
            missing = current_quality_raters - final_base_keys
            if missing:
                raise ValueError(
                    "Recomputed queue annotations are missing keys for the current "
                    f"raters: {sorted(missing)}."
                )
        else:
            # Raters match: keep the stored queue untouched and only verify that
            # every entry is consistent, so no scoring work is redone.
            self._infer_rater_names(self.queue)
            stored_base_keys = self._base_rater_names(
                set(self.queue[0][0].keys())
            )
            missing = current_quality_raters - stored_base_keys
            if missing:
                raise ValueError(
                    "Queue annotations claim to match the current raters but are "
                    f"missing keys: {sorted(missing)}."
                )
            logger.info(
                "Quality raters match the state file; reusing the stored queue "
                "without recomputing scores."
            )

        self._verify_resume_against_outputs(iter_num, num_selected_docs)
        resume_state = ResumeState(
            iter_num=iter_num,
            num_selected_docs=num_selected_docs,
            queue_size=len(self.queue),
            dropped_docs=dropped_docs,
            raters_recomputed=raters_recomputed,
        )
        logger.info(
            f"Resume point: continue at iteration {iter_num}, "
            f"{num_selected_docs} docs already selected, queue size "
            f"{resume_state.queue_size}"
            + (f", {dropped_docs} docs dropped during recomputation" if dropped_docs else "")
        )
        return resume_state
