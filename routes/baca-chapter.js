const express = require("express");
const router = express.Router();
const { getBacaChapter } = require("../controllers/bacaChapterController");

/**
 * @swagger
 * /baca-chapter/{slug}/{chapter}:
 *   get:
 *     summary: Get chapter content for reading
 *     description: Retrieve chapter images and metadata for a specific manga chapter
 *     tags:
 *       - Chapter
 *     parameters:
 *       - in: path
 *         name: slug
 *         required: true
 *         schema:
 *           type: string
 *         description: Manga slug identifier
 *         example: bocchi-the-rock
 *       - in: path
 *         name: chapter
 *         required: true
 *         schema:
 *           type: string
 *         description: Chapter number
 *         example: "82"
 *     responses:
 *       200:
 *         description: Chapter content retrieved successfully
 *         content:
 *           application/json:
 *             schema:
 *               type: object
 *               properties:
 *                 title:
 *                   type: string
 *                   example: "Bocchi the Rock! Chapter 82"
 *                 mangaInfo:
 *                   type: object
 *                   properties:
 *                     title:
 *                       type: string
 *                     originalLink:
 *                       type: string
 *                     apiLink:
 *                       type: string
 *                     slug:
 *                       type: string
 *                 description:
 *                   type: string
 *                 images:
 *                   type: array
 *                   items:
 *                     type: object
 *                     properties:
 *                       src:
 *                         type: string
 *                       alt:
 *                         type: string
 *                       id:
 *                         type: string
 *                       fallbackSrc:
 *                         type: string
 *                 meta:
 *                   type: object
 *                   properties:
 *                     chapterNumber:
 *                       type: string
 *                     totalImages:
 *                       type: number
 *                     publishDate:
 *                       type: string
 *                     slug:
 *                       type: string
 *                 navigation:
 *                   type: object
 *                   properties:
 *                     prevChapter:
 *                       type: object
 *                     nextChapter:
 *                       type: object
 *                     allChapters:
 *                       type: string
 *                 attempts:
 *                   type: array
 *                   description: Riwayat percobaan kandidat URL, berurutan
 *                   items:
 *                     type: object
 *                     properties:
 *                       url:
 *                         type: string
 *                       chapter:
 *                         type: string
 *                       outcome:
 *                         type: string
 *                         enum: [ok, not_found, blocked, chapter_mismatch, parse_failed, error]
 *                       statusCode:
 *                         type: integer
 *                         nullable: true
 *                       detail:
 *                         type: string
 *       404:
 *         description: Chapter not found (detail menyebutkan variasi nomor yang sudah dicoba, disertai attempts)
 *       502:
 *         description: Halaman berhasil diambil tetapi judul/gambar tidak bisa diparse (disertai source, missing, attempts)
 *       503:
 *         description: Upstream Cloudflare protection aktif
 *       500:
 *         description: Internal server error
 */
router.get("/:slug/:chapter", getBacaChapter);

module.exports = router;
