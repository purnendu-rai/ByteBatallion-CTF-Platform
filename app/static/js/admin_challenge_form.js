// Admin challenge form helpers.
//
// This used to be an inline <script> in admin/challenge_form.html with
// inline onchange/oninput attributes. It lives here now so the
// Content-Security-Policy can forbid inline scripts and inline event
// handlers outright (see SECURITY.md) — the handlers are bound with
// addEventListener below instead of attributes.

function toggleCustomCategory() {
  var sel = document.getElementById('category-select');
  var inp = document.getElementById('custom-category');
  if (!sel || !inp) return;
  if (sel.value === '__custom__') {
    inp.style.display = '';
    inp.focus();
  } else {
    inp.style.display = 'none';
  }
}

// Build the requested number of file <input> boxes.
function buildFileInputs() {
  var countInput = document.getElementById('file-count');
  var box = document.getElementById('file-inputs');
  if (!countInput || !box) return;
  var n = parseInt(countInput.value, 10);
  if (isNaN(n) || n < 0) n = 0;
  if (n > 20) n = 20;
  box.innerHTML = '';
  for (var i = 1; i <= n; i++) {
    var label = document.createElement('label');
    label.textContent = 'File ' + i;
    var input = document.createElement('input');
    input.type = 'file';
    input.name = 'files';
    label.appendChild(input);
    box.appendChild(label);
  }
}

// On load, honour an existing selection and build the boxes.
document.addEventListener('DOMContentLoaded', function () {
  var sel = document.getElementById('category-select');
  if (sel) sel.addEventListener('change', toggleCustomCategory);
  var countInput = document.getElementById('file-count');
  if (countInput) {
    countInput.addEventListener('change', buildFileInputs);
    countInput.addEventListener('input', buildFileInputs);
  }
  toggleCustomCategory();
  buildFileInputs();
});
