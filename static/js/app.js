// PHOENIXMARKET page helpers. Kept out of inline attributes so the
// Content-Security-Policy can forbid inline event handlers.

(function () {
  // <form data-confirm="Question?"> asks for confirmation before submitting.
  document.addEventListener(
    "submit",
    function (event) {
      var form = event.target;
      var question = form && form.getAttribute ? form.getAttribute("data-confirm") : null;
      if (question && !window.confirm(question)) {
        event.preventDefault();
      }
    },
    true
  );

  // <button data-copy="text" data-copied="Copied"> copies text to the clipboard.
  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("[data-copy]") : null;
    if (!button || !navigator.clipboard) {
      return;
    }
    var original = button.textContent;
    navigator.clipboard.writeText(button.getAttribute("data-copy")).then(function () {
      button.textContent = button.getAttribute("data-copied") || original;
      window.setTimeout(function () {
        button.textContent = original;
      }, 1500);
    });
  });
})();
