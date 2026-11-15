javascript:(function() {
    /* --- PREVENT DUPLICATES --- */
    if (document.getElementById('msg-scraper-panel')) return;

    /* --- STATE VARIABLES --- */
    // messageHistory is ALWAYS kept in true conversation order. New rows are
    // inserted next to the message they were seen beside, never by batch or by
    // scroll direction, so scrolling up and down can interleave safely.
    let messageHistory = [];
    const recordsByKey = new Map();      // internal key -> record
    const stableIdToKey = new Map();     // DOM message id -> internal key
    const rowKeyCache = new WeakMap();   // row element -> internal key
    const contentOrdinals = new Map();   // content hash -> how many copies seen
    const usedExportIds = new Set();     // export id uniqueness
    let recordSeq = 0;

    let isRecording = false;
    let scraperInterval = null;

    // Two scans of the same screen can report a pixel or two of round-off.
    const POSITION_TOLERANCE = 8;

    // Auto-Scroll Variables
    let isScrolling = false;
    let scrollSpeed = 2;
    let scrollDir = -1;
    let scrollFrameId = null;
    let scrollTargetElement = null; // The "Detective" stores the target here

    /* --- DETECTIVE FUNCTION --- */
    // This finds the correct box to scroll (instead of the window)
    function findScrollableTarget() {
        // 1. Get all DIVs on the page
        const candidates = document.querySelectorAll('div');
        let largest = null;
        let maxArea = 0;

        // 2. Loop through them
        candidates.forEach(el => {
            // Must have a scrollbar (scrollHeight > clientHeight)
            if (el.scrollHeight > el.clientHeight) {
                const style = window.getComputedStyle(el);
                // Must have overflow set to auto or scroll
                if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                    // 3. Pick the largest one by area (Width x Height)
                    // This avoids scrolling small sidebars or chat lists
                    const area = el.clientWidth * el.clientHeight;
                    if (area > maxArea) {
                        maxArea = area;
                        largest = el;
                    }
                }
            }
        });

        // If no div found, default to window (fallback)
        return largest || window;
    }

    /* --- CHAT CONTAINER HELPERS --- */
    // Locate the element that actually scrolls the conversation. Deriving it
    // from a message row keeps us correct even when the page has several
    // scrollable sidebars.
    function isScrollableElement(el) {
        if (!el || el === document.body || el === document.documentElement) return false;
        if (el.scrollHeight <= el.clientHeight + 1) return false;
        const style = window.getComputedStyle(el);
        return /(auto|scroll|overlay)/.test(style.overflowY);
    }

    function closestScrollableAncestor(el) {
        let node = el;
        while (node && node !== document.body && node !== document.documentElement) {
            if (isScrollableElement(node)) return node;
            node = node.parentElement;
        }
        return null;
    }

    function resolveChatContainer(mainChat, firstRow) {
        let container = firstRow ? closestScrollableAncestor(firstRow) : null;
        if (!container && isScrollableElement(mainChat)) container = mainChat;

        if (!container) {
            // Fall back to the largest scrollable box inside the message area
            // that actually contains message rows.
            const candidates = mainChat.querySelectorAll('div');
            let best = null;
            let bestArea = 0;
            candidates.forEach(el => {
                if (!isScrollableElement(el)) return;
                if (!el.querySelector('div[role="row"]')) return;
                const area = el.clientWidth * el.clientHeight;
                if (area > bestArea) {
                    bestArea = area;
                    best = el;
                }
            });
            container = best;
        }

        return container || window;
    }

    function getContentMetrics(container) {
        if (container && container !== window && container.nodeType === 1) {
            const rect = container.getBoundingClientRect();
            return {
                top: rect.top,
                bottom: rect.bottom,
                scrollTop: container.scrollTop || 0,
                scrollHeight: container.scrollHeight
            };
        }
        const docEl = document.documentElement;
        const body = document.body;
        const scrollTop = window.scrollY ||
            (docEl ? docEl.scrollTop : 0) ||
            (body ? body.scrollTop : 0) || 0;
        return {
            top: 0,
            bottom: window.innerHeight,
            scrollTop: scrollTop,
            scrollHeight: Math.max(
                docEl ? docEl.scrollHeight : 0,
                body ? body.scrollHeight : 0
            )
        };
    }

    // Two content-space coordinates per row:
    //  - `offset`: distance from the BOTTOM of the loaded conversation. Stable
    //    when older history is prepended above the view, so it is used to
    //    recognise the same message again.
    //  - `top`: distance from the TOP of the loaded conversation. Stable when
    //    new messages are appended below, so it is used to slot in a scan that
    //    shares no row with the recorded history.
    function getRowCoords(row, metrics) {
        const rowRect = row.getBoundingClientRect();
        const top = rowRect.top - metrics.top + metrics.scrollTop;
        return {
            top: Math.round(top * 100) / 100,
            offset: Math.round((top - metrics.scrollHeight) * 100) / 100
        };
    }

    function isRowVisible(row, metrics) {
        const rowRect = row.getBoundingClientRect();
        return rowRect.bottom > metrics.top && rowRect.top < metrics.bottom;
    }

    /* --- MESSAGE IDENTITY HELPERS --- */
    // Only message-specific attributes are trusted as identity. Generic `id`
    // values on Messenger's wrapper nodes are not guaranteed to be message ids.
    const MESSAGE_ID_ATTRS = [
        'data-message-id', 'data-messageid', 'data-message_id',
        'data-msg-id', 'data-msgid', 'data-mid'
    ];
    const MESSAGE_ID_SELECTOR = MESSAGE_ID_ATTRS.map(attr => `[${attr}]`).join(',');

    // Prefer a real message identifier from the DOM. Messenger rows expose
    // different attributes across builds, so check the row and its descendants.
    function getStableMessageId(row) {
        const nodes = [row];
        const quoted = row.querySelectorAll(MESSAGE_ID_SELECTOR);
        quoted.forEach(node => nodes.push(node));

        for (let i = 0; i < nodes.length; i++) {
            const node = nodes[i];
            if (!node.getAttribute) continue;
            for (let j = 0; j < MESSAGE_ID_ATTRS.length; j++) {
                const value = node.getAttribute(MESSAGE_ID_ATTRS[j]);
                if (value) return value;
            }
        }
        return null;
    }

    function normalizeText(text) {
        return text.replace(/\s+/g, ' ').trim();
    }

    function hashText(text) {
        let hash = 5381;
        for (let i = 0; i < text.length; i++) {
            hash = ((hash << 5) + hash + text.charCodeAt(i)) | 0;
        }
        return (hash >>> 0).toString(36);
    }

    function extractRowContent(row, applyHighlight) {
        let rowParts = [];
        let rowType = "";
        const bubbles = row.querySelectorAll('div[dir="auto"], img, video');
        const screenMiddle = window.innerWidth / 2;

        bubbles.forEach(bubble => {
            if (bubble.tagName === 'IMG') {
                const width = bubble.getBoundingClientRect().width;
                const src = bubble.src || "";
                if (width < 40 && !src.includes('emoji')) return;
            }
            const bubbleRect = bubble.getBoundingClientRect();
            const type = bubbleRect.left > screenMiddle ? "Sent" : "Received";
            if (!rowType) rowType = type;

            let content = "";
            if (bubble.tagName === 'DIV') content = bubble.innerText.trim();
            else if (bubble.tagName === 'IMG') content = bubble.getAttribute('alt') || "[Image]";
            else if (bubble.tagName === 'VIDEO') content = "[Video]";

            if (!content) return;
            if (applyHighlight !== false) {
                bubble.style.border = type === "Sent" ? "3px solid #00ff00" : "3px solid #0000ff";
                bubble.style.boxSizing = "border-box";
            }
            rowParts.push(content);
        });

        if (rowParts.length === 0) return null;
        return { type: rowType, text: rowParts.join(" ") };
    }

    /* --- RECORD HELPERS --- */
    function contentMatches(record, type, matchText) {
        return record.type === type && record.matchText === matchText;
    }

    function nearSameSpot(record, coords) {
        return Math.abs(record.offset - coords.offset) <= POSITION_TOLERANCE ||
            Math.abs(record.top - coords.top) <= POSITION_TOLERANCE;
    }

    // Find the record a visible row already corresponds to. Returns null for a
    // genuinely new message. `claimedKeys` prevents one record being matched to
    // two different rows during the same scan.
    function findExistingRecord(row, stableId, type, matchText, coords, claimedKeys) {
        // Same DOM node seen before (fast path). A recycled virtual-list node
        // is only trusted when the content is unchanged or the row still sits
        // at the same spot; otherwise it now shows a different message.
        if (rowKeyCache.has(row)) {
            const cachedKey = rowKeyCache.get(row);
            const cachedRecord = recordsByKey.get(cachedKey);
            if (cachedRecord && !claimedKeys.has(cachedKey) &&
                (contentMatches(cachedRecord, type, matchText) || nearSameSpot(cachedRecord, coords))) {
                return cachedRecord;
            }
        }

        // Same real message id (survives re-render / node replacement). A real
        // id identifies the message even if its text changed (edit / late media
        // load), but only trust it while the row is still at the same spot, so
        // a quoted-message id repeated in another row cannot merge two rows.
        if (stableId && stableIdToKey.has(stableId)) {
            const idKey = stableIdToKey.get(stableId);
            const idRecord = recordsByKey.get(idKey);
            if (idRecord && !claimedKeys.has(idKey) &&
                (contentMatches(idRecord, type, matchText) || nearSameSpot(idRecord, coords))) {
                return idRecord;
            }
        }

        // No id: match the same text recorded at (essentially) the same spot.
        // Distinct messages never share both.
        let bestRecord = null;
        let bestDelta = Infinity;
        for (let i = 0; i < messageHistory.length; i++) {
            const record = messageHistory[i];
            if (claimedKeys.has(record.key)) continue;
            if (!contentMatches(record, type, matchText)) continue;
            const delta = Math.min(
                Math.abs(record.offset - coords.offset),
                Math.abs(record.top - coords.top)
            );
            if (delta <= POSITION_TOLERANCE && delta < bestDelta) {
                bestDelta = delta;
                bestRecord = record;
            }
        }
        return bestRecord;
    }

    function makeExportId(stableId, textHash, ordinal) {
        let id;
        if (stableId && !usedExportIds.has(stableId)) {
            id = stableId;
        } else {
            const base = stableId || `msg_${textHash}`;
            id = `${base}#${ordinal}`;
        }
        usedExportIds.add(id);
        return id;
    }

    function attachRow(record, row) {
        if (!row) return;
        rowKeyCache.set(row, record.key);
        // WeakRef keeps a handle on the row without pinning it in memory; it is
        // used to re-measure the conversation when no recorded row is on screen.
        record.rowRef = typeof WeakRef === 'function' ? new WeakRef(row) : { deref: () => row };
    }

    function createRecord(row, stableId, type, text, matchText, coords) {
        const seq = ++recordSeq;
        const key = `k${seq}`;
        const textHash = hashText(type + '\u0000' + matchText);
        const ordinal = (contentOrdinals.get(textHash) || 0) + 1;
        contentOrdinals.set(textHash, ordinal);

        const record = {
            type: type,
            text: text,
            id: makeExportId(stableId, textHash, ordinal),
            matchText: matchText,
            top: coords.top,
            offset: coords.offset,
            key: key,
            rowRef: null
        };

        recordsByKey.set(key, record);
        if (stableId && !stableIdToKey.has(stableId)) stableIdToKey.set(stableId, key);
        attachRow(record, row);
        return record;
    }

    function updateRecord(record, stableId, type, text, matchText, coords, row) {
        // Same message seen again: refresh its content, but never move it or
        // append a second copy. Empty text (e.g. media still loading) must not
        // erase what was already captured.
        if (text) {
            record.type = type;
            record.text = text;
            record.matchText = matchText;
        }
        record.top = coords.top;
        record.offset = coords.offset;
        attachRow(record, row);
        if (stableId && !stableIdToKey.has(stableId)) stableIdToKey.set(stableId, record.key);
    }

    /* --- ORDERING --- */
    // Bring stored coordinates into the current content space. The conversation
    // can grow in two ways:
    //  - older history prepended above: `offset` (bottom-anchored) is unchanged,
    //    `top` (top-anchored) shifts down by the inserted height;
    //  - new messages appended below: `top` is unchanged, `offset` shifts down.
    // Anchors already recorded reveal which happened, so both coordinates can
    // be corrected independently and remain comparable to the new scan.
    function rebaseStoredCoords(rowInfos, metrics) {
        const anchors = [];

        rowInfos.forEach(info => {
            let record = null;
            if (info.stableId && stableIdToKey.has(info.stableId)) {
                record = recordsByKey.get(stableIdToKey.get(info.stableId));
            } else if (rowKeyCache.has(info.row)) {
                record = recordsByKey.get(rowKeyCache.get(info.row));
            }
            if (!record) return;
            if (!contentMatches(record, info.type, info.matchText)) return;
            anchors.push({ record: record, info: info });
        });

        if (anchors.length === 0) {
            // Nothing recorded is on screen. Still re-measure any recorded row
            // that is currently attached to the DOM (outside the viewport), so
            // a coordinate shift caused by prepends/appends is corrected before
            // an anchor-less run is slotted in.
            messageHistory.forEach(record => {
                const row = record.rowRef && record.rowRef.deref ? record.rowRef.deref() : null;
                if (!row || !row.isConnected) return;
                const rect = row.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) return;
                record.top = Math.round((rect.top - metrics.top + metrics.scrollTop) * 100) / 100;
                record.offset = Math.round((record.top - metrics.scrollHeight) * 100) / 100;
            });
            return;
        }

        // Use all anchors to find the common shift of each coordinate. A single
        // shifted coordinate is enough to identify prepend vs append.
        const topDeltas = [];
        const offsetDeltas = [];
        const seen = new Set();
        anchors.forEach(anchor => {
            if (seen.has(anchor.record.key)) return;
            seen.add(anchor.record.key);
            topDeltas.push(anchor.info.top - anchor.record.top);
            offsetDeltas.push(anchor.info.offset - anchor.record.offset);
        });

        const median = values => {
            const sorted = values.slice().sort((a, b) => a - b);
            return sorted[Math.floor(sorted.length / 2)];
        };
        const topShift = median(topDeltas);
        const offsetShift = median(offsetDeltas);

        if (Math.abs(topShift) >= 1) {
            messageHistory.forEach(record => { record.top += topShift; });
        }
        if (Math.abs(offsetShift) >= 1) {
            messageHistory.forEach(record => { record.offset += offsetShift; });
        }
    }

    // Insert the rows of one scan into the true-order list. A run of new rows
    // is placed directly next to an anchor that was already recorded; when a
    // scan shares nothing with the recorded history (a large jump), it is
    // slotted in by its coordinates in the conversation. Direction of scrolling
    // never decides where a message goes.
    function insertScan(entries) {
        let i = 0;
        while (i < entries.length) {
            if (!entries[i].isNew) { i++; continue; }

            let j = i;
            while (j < entries.length && entries[j].isNew) j++;

            let anchorBefore = null;
            for (let k = i - 1; k >= 0; k--) {
                if (!entries[k].isNew) { anchorBefore = entries[k].record; break; }
            }
            let anchorAfter = null;
            for (let k = j; k < entries.length; k++) {
                if (!entries[k].isNew) { anchorAfter = entries[k].record; break; }
            }

            const run = entries.slice(i, j).map(entry => entry.record);

            if (anchorBefore) {
                const at = messageHistory.indexOf(anchorBefore);
                messageHistory.splice(at + 1, 0, ...run);
            } else if (anchorAfter) {
                const at = messageHistory.indexOf(anchorAfter);
                messageHistory.splice(at < 0 ? messageHistory.length : at, 0, ...run);
            } else {
                // No shared row was on screen. Prefer real document order of
                // any recorded row still mounted in the DOM; fall back to
                // measured coordinates if none are available.
                if (!insertRunByDomOrder(run)) insertRunByCoords(run);
            }
            i = j;
        }
    }

    // Place an anchor-less run using document order of recorded rows that are
    // still mounted (even far outside the viewport). Document order is the
    // conversation's true order, so this is immune to scrolling and to the
    // conversation growing above or below.
    function insertRunByDomOrder(run) {
        if (run.length === 0) return false;
        const lastRow = run[run.length - 1].rowRef && run[run.length - 1].rowRef.deref
            ? run[run.length - 1].rowRef.deref()
            : null;
        const firstRow = run[0].rowRef && run[0].rowRef.deref
            ? run[0].rowRef.deref()
            : null;
        if (!lastRow || !firstRow || !lastRow.isConnected) return false;

        const isBefore = (a, b) => !!(a.compareDocumentPosition(b) & 4 /* DOCUMENT_POSITION_FOLLOWING */);

        let at = -1;
        let mountedCount = 0;
        for (let k = 0; k < messageHistory.length; k++) {
            const mounted = mountedRow(messageHistory[k]);
            if (!mounted) continue;
            mountedCount++;
            if (isBefore(lastRow, mounted)) { at = k; break; }
        }
        if (at === -1) {
            // No mounted recorded row follows the run. That only proves the run
            // is last when every recorded row is still mounted; otherwise some
            // unmounted row could belong after it, so fall back to coordinates.
            if (mountedCount === 0 || mountedCount < messageHistory.length) return false;
            at = messageHistory.length;
        }
        messageHistory.splice(at, 0, ...run);
        return true;
    }

    function mountedRow(record) {
        const row = record.rowRef && record.rowRef.deref ? record.rowRef.deref() : null;
        return row && row.isConnected ? row : null;
    }

    function insertRunByCoords(run) {
        const maxTop = Math.max(...run.map(r => r.top));
        const maxOffset = Math.max(...run.map(r => r.offset));

        // Slot before the first recorded message that is after the whole run in
        // BOTH coordinate spaces. Using both makes a wrong guess impossible
        // when only one coordinate moved (prepend vs append).
        let at = messageHistory.length;
        for (let k = 0; k < messageHistory.length; k++) {
            const record = messageHistory[k];
            if (record.top > maxTop + POSITION_TOLERANCE &&
                record.offset > maxOffset + POSITION_TOLERANCE) {
                at = k;
                break;
            }
        }
        messageHistory.splice(at, 0, ...run);
    }

    /* --- CREATE GUI --- */
    const panel = document.createElement('div');
    panel.id = 'msg-scraper-panel';
    panel.style.cssText = 'position:fixed; top:80px; right:20px; width:240px; background:#111; color:white; padding:15px; border-radius:10px; z-index:9999; font-family:sans-serif; box-shadow: 0 6px 16px rgba(0,0,0,0.7); border: 1px solid #444;';

    const title = document.createElement('h3');
    title.innerText = "Messenger Scraper (Targeted)";
    title.style.cssText = 'margin:0 0 5px 0; font-size:15px; text-align:center; color:#ffcc00;';

    const counter = document.createElement('div');
    counter.innerText = "0 Messages";
    counter.style.cssText = 'font-size:20px; text-align:center; margin-bottom:15px; font-weight:bold; color:#fff;';

    const btnRecord = document.createElement('button');
    btnRecord.innerText = "▶ START RECORDING";
    btnRecord.style.cssText = 'width:100%; padding:10px; margin-bottom:15px; background:#28a745; color:white; border:none; border-radius:5px; cursor:pointer; font-weight:bold; letter-spacing:1px;';

    const scrollLabel = document.createElement('div');
    scrollLabel.innerText = "SMART SCROLL";
    scrollLabel.style.cssText = 'font-size:10px; color:#aaa; margin-bottom:5px; text-align:center; border-top: 1px solid #333; padding-top:10px;';

    const scrollBtnContainer = document.createElement('div');
    scrollBtnContainer.style.cssText = 'display:flex; gap:5px; margin-bottom:10px;';

    const btnScrollUp = document.createElement('button');
    btnScrollUp.innerText = "⬆ UP";
    btnScrollUp.style.cssText = 'flex:1; padding:8px; background:#444; color:white; border:none; border-radius:4px; cursor:pointer; font-weight:bold;';

    const btnScrollStop = document.createElement('button');
    btnScrollStop.innerText = "⏹";
    btnScrollStop.style.cssText = 'width:40px; padding:8px; background:#dc3545; color:white; border:none; border-radius:4px; cursor:pointer; font-weight:bold;';

    const btnScrollDown = document.createElement('button');
    btnScrollDown.innerText = "⬇ DOWN";
    btnScrollDown.style.cssText = 'flex:1; padding:8px; background:#444; color:white; border:none; border-radius:4px; cursor:pointer; font-weight:bold;';

    const speedContainer = document.createElement('div');
    speedContainer.style.cssText = 'display:flex; align-items:center; gap:10px; margin-bottom:15px;';
    const speedInput = document.createElement('input');
    speedInput.type = "range";
    speedInput.min = "1";
    speedInput.max = "50";
    speedInput.value = "2";
    speedInput.style.cssText = 'flex:1; cursor:pointer;';
    const speedVal = document.createElement('span');
    speedVal.innerText = "Speed: 2";
    speedVal.style.cssText = 'font-size:12px; color:#aaa; width:55px; text-align:right;';

    speedContainer.appendChild(speedInput);
    speedContainer.appendChild(speedVal);
    scrollBtnContainer.appendChild(btnScrollUp);
    scrollBtnContainer.appendChild(btnScrollStop);
    scrollBtnContainer.appendChild(btnScrollDown);

    const btnSave = document.createElement('button');
    btnSave.innerText = "💾 SAVE JSON";
    btnSave.style.cssText = 'width:100%; padding:10px; background:#007bff; color:white; border:none; border-radius:5px; cursor:pointer; font-weight:bold; display:none; margin-top:5px;';

    const btnClose = document.createElement('button');
    btnClose.innerText = "❌ Close";
    btnClose.style.cssText = 'margin-top:10px; background:none; border:none; color:#555; width:100%; cursor:pointer; font-size:11px;';

    panel.appendChild(title);
    panel.appendChild(counter);
    panel.appendChild(btnRecord);
    panel.appendChild(scrollLabel);
    panel.appendChild(scrollBtnContainer);
    panel.appendChild(speedContainer);
    panel.appendChild(btnSave);
    panel.appendChild(btnClose);
    document.body.appendChild(panel);

    /* --- SCROLL LOGIC --- */
    function scrollTick() {
        if (!isScrolling || !scrollTargetElement) return;

        // SCROLL THE TARGET, NOT THE WINDOW
        scrollTargetElement.scrollBy(0, scrollDir * scrollSpeed);

        scrollFrameId = requestAnimationFrame(scrollTick);
    }

    function setScroll(state, dir) {
        isScrolling = state;
        if (dir) scrollDir = dir;

        if (state) {
            // RUN THE DETECTIVE!
            scrollTargetElement = findScrollableTarget();

            // Visual check: Highlight the box we found so you know it's working
            if (scrollTargetElement !== window) {
                scrollTargetElement.style.boxShadow = "inset 0 0 20px rgba(0,255,0,0.2)";
                console.log("Found scroll target:", scrollTargetElement);
            }

            cancelAnimationFrame(scrollFrameId);
            scrollTick();

            // Update Buttons
            btnScrollUp.style.background = (scrollDir === -1) ? '#28a745' : '#444';
            btnScrollDown.style.background = (scrollDir === 1) ? '#28a745' : '#444';
        } else {
            cancelAnimationFrame(scrollFrameId);
            btnScrollUp.style.background = '#444';
            btnScrollDown.style.background = '#444';
            if (scrollTargetElement && scrollTargetElement.style) {
                 scrollTargetElement.style.boxShadow = "none";
            }
        }
    }

    /* --- SCRAPER LOGIC --- */
    function scanMessages() {
        const mainChat = document.querySelector('[role="main"]');
        if (!mainChat) return;

        const rows = mainChat.querySelectorAll('div[role="row"]');
        if (rows.length === 0) return;

        const container = resolveChatContainer(mainChat, rows[0]);

        // PASS 1: read geometry and content without touching the DOM layout.
        // Highlight borders are applied later, after positions are measured.
        const metrics = getContentMetrics(container);
        const rowInfos = [];

        rows.forEach(row => {
            if (!isRowVisible(row, metrics)) return;
            const content = extractRowContent(row, false);
            if (!content) return;

            rowInfos.push({
                row: row,
                stableId: getStableMessageId(row),
                type: content.type,
                text: content.text,
                matchText: normalizeText(content.text),
                coords: getRowCoords(row, metrics)
            });
        });

        // PASS 2: bring stored coordinates into the current content space
        // before comparing anything to them.
        rebaseStoredCoords(rowInfos, metrics);

        // PASS 3: resolve every visible row to an existing record or a new one.
        const claimedKeys = new Set();
        const entries = [];

        rowInfos.forEach(info => {
            const existing = findExistingRecord(
                info.row,
                info.stableId,
                info.type,
                info.matchText,
                info.coords,
                claimedKeys
            );

            if (existing) {
                updateRecord(
                    existing,
                    info.stableId,
                    info.type,
                    info.text,
                    info.matchText,
                    info.coords,
                    info.row
                );
                claimedKeys.add(existing.key);
                entries.push({ record: existing, isNew: false });
                return;
            }

            const record = createRecord(
                info.row,
                info.stableId,
                info.type,
                info.text,
                info.matchText,
                info.coords
            );
            claimedKeys.add(record.key);
            entries.push({ record: record, isNew: true });
        });

        // PASS 4: splice new rows into true conversation order.
        if (entries.length > 0) insertScan(entries);

        // PASS 5: visual feedback only (no further measurements depend on it).
        rowInfos.forEach(info => {
            extractRowContent(info.row, true);
        });

        counter.innerText = `${messageHistory.length} Messages`;
    }

    /* --- LISTENERS --- */
    btnRecord.onclick = function() {
        if (!isRecording) {
            isRecording = true;
            btnRecord.innerText = "⏸ PAUSE RECORDING";
            btnRecord.style.backgroundColor = "#ffc107";
            btnRecord.style.color = "black";
            btnSave.style.display = "block";
            scanMessages();
            scraperInterval = setInterval(scanMessages, 800);
        } else {
            isRecording = false;
            clearInterval(scraperInterval);
            btnRecord.innerText = "▶ RESUME RECORDING";
            btnRecord.style.backgroundColor = "#28a745";
            btnRecord.style.color = "white";
        }
    };

    btnScrollUp.onclick = () => setScroll(true, -1);
    btnScrollDown.onclick = () => setScroll(true, 1);
    btnScrollStop.onclick = () => setScroll(false, null);

    speedInput.oninput = function() {
        scrollSpeed = parseInt(this.value);
        speedVal.innerText = `Speed: ${this.value}`;
    };

    btnSave.onclick = function() {
        setScroll(false);
        isRecording = false;
        clearInterval(scraperInterval);
        btnRecord.innerText = "▶ START RECORDING";
        btnRecord.style.backgroundColor = "#28a745";

        // Capture whatever is on screen right now before exporting.
        scanMessages();

        // Export only the stable, public fields. `position` is the message's
        // index in true conversation order; `id` identifies the message.
        const exported = messageHistory.map((record, index) => ({
            type: record.type,
            text: record.text,
            id: record.id,
            position: index
        }));

        const blob = new Blob([JSON.stringify(exported, null, 2)], {type: "application/json"});
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "messenger_chat.json";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        counter.innerText = `Saved ${exported.length} items!`;
    };

    btnClose.onclick = function() {
        setScroll(false);
        clearInterval(scraperInterval);
        panel.remove();
    };
})();
