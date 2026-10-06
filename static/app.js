/* PHSA Recorder: the only script of our own. It runs after htmx has loaded.
   htmx 4 swaps every response into the page except 204 and 304. We also keep
   out server errors (5xx) and the two plain-text answers "not found" (404)
   and "already superseded" (409), and show one plain line for them. A 422
   is NOT kept out: it is the form coming back with its messages. */
htmx.config.noSwap = [204, 304, 404, 409, "5xx"];

document.addEventListener("htmx:response:error", function (event) {
  var status = event.detail.ctx.response.status;
  if (status === 422) {
    return;
  }
  var line = document.getElementById("request-problem");
  if (!line) {
    return;
  }
  if (status === 409) {
    line.textContent = "That version was already superseded by someone else. Reload the page to see the current one.";
  } else if (status === 404) {
    line.textContent = "That record was not found. Reload the page.";
  } else {
    line.textContent = "Something went wrong. Nothing was saved. Reload the page and try again.";
  }
  line.hidden = false;
});
