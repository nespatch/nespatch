/* Cut one original page out of a PDF.
   The page is copied whole. Nothing is redrawn. */
(function () {
  var PDFJS_SRC = "https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.min.js";
  var PDFJS_WORKER = "https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js";
  var PDFLIB_SRC = "https://cdn.jsdelivr.net/npm/pdf-lib@1.17.1/dist/pdf-lib.min.js";

  function loadScript(src, ready) {
    if (ready()) return Promise.resolve();
    return new Promise(function (resolve, reject) {
      var script = document.createElement("script");
      script.src = src;
      script.onload = function () { resolve(); };
      script.onerror = function () { reject(new Error(src)); };
      document.head.appendChild(script);
    });
  }

  function textChars(textContent) {
    var chars = 0;
    var items = textContent.items || [];
    for (var i = 0; i < items.length; i++) {
      if (items[i].str) chars += items[i].str.replace(/\s/g, "").length;
    }
    return chars;
  }

  function middleRange(total) {
    if (total < 1) return [1, 1];
    var start = Math.floor(total / 3) + 1;
    var end = Math.ceil((total * 2) / 3);
    if (start < 1) start = 1;
    if (end > total) end = total;
    if (end < start) end = start;
    return [start, end];
  }

  function cutOriginalPage(data) {
    var bytes = data instanceof ArrayBuffer ? new Uint8Array(data) : new Uint8Array(data);
    var forPdfjs = bytes.slice().buffer;
    var forLib = bytes.slice().buffer;
    return loadScript(PDFJS_SRC, function () { return window.pdfjsLib; }).then(function () {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;
      return window.pdfjsLib.getDocument({ data: forPdfjs }).promise;
    }).then(function (doc) {
      var total = doc.numPages;
      var range = middleRange(total);
      var start = range[0];
      var end = range[1];
      var center = (start + end) / 2;
      var best = start;
      var bestChars = -1;
      function step(n) {
        if (n > end) {
          doc.destroy();
          return {
            total: total,
            page: best,
            chars: Math.max(bestChars, 0),
            start: start,
            end: end
          };
        }
        return doc.getPage(n).then(function (page) {
          return page.getTextContent();
        }).then(function (text) {
          var chars = textChars(text);
          var closer = Math.abs(n - center) < Math.abs(best - center);
          if (chars > bestChars || (chars === bestChars && closer)) {
            bestChars = chars;
            best = n;
          }
          return step(n + 1);
        });
      }
      return step(start);
    }).then(function (choice) {
      return loadScript(PDFLIB_SRC, function () { return window.PDFLib; }).then(function () {
        return window.PDFLib.PDFDocument.load(forLib);
      }).then(function (src) {
        return window.PDFLib.PDFDocument.create().then(function (out) {
          return out.copyPages(src, [choice.page - 1]).then(function (copied) {
            out.addPage(copied[0]);
            return out.save();
          });
        });
      }).then(function (saved) {
        choice.bytes = saved;
        return choice;
      });
    });
  }

  window.nespatchCutPage = cutOriginalPage;
})();
