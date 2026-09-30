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
})();
