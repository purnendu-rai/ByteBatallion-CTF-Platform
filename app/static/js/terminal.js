// Home-page terminal (v2.3): a working mini-shell, not a decoration.
//
// It behaves like a real terminal emulator: window chrome (see
// index.html), a login banner, a prompt that follows the current
// directory, command history (ArrowUp/Down), Tab completion, Ctrl+C
// (cancel line) and Ctrl+L (clear). Clicking the terminal focuses the
// (hidden) input; a blinking block cursor renders the current line.
//
// SECURITY (v2.4) — read before wiring anything new into this shell:
// this terminal is 100% client-side. It makes NO network calls and can
// never read the server, the database, flags, or other users. The `bb`
// commands are text stubs only. That must stay true: any future `bb`
// handler that touches real data MUST call a session-authenticated
// server endpoint, MUST refuse anonymous users, and MUST return only
// the caller's own data (per-user flags are stored as hashes — a
// handler must never print another user's flag, or any plaintext flag).
// The `bb challenges` / `bb flag` stubs below already enforce the
// sign-in gate client-side; the server endpoint must enforce it again.
//
// EXTENSION POINT — future challenge/flag use: every command lives in
// the COMMANDS registry below (name -> {desc, run}). To wire the shell
// to the platform later (e.g. `bb challenges` listing the board, or a
// `flag <challenge>` lookup), add one entry here — the `bb` command
// already reserves that namespace and its stubs say exactly what the
// handler needs (a signed-in session). No other file has to change.

(function () {
  var term = document.getElementById('terminal');
  if (!term) return;

  var output = document.getElementById('terminal-output');
  var input = document.getElementById('terminal-input');
  var inputText = document.getElementById('terminal-input-text');
  var promptEl = document.getElementById('terminal-prompt');
  var username = term.getAttribute('data-username') || 'guest';
  // v2.4: the home page is public, so the terminal must know whether
  // the visitor is signed in — platform commands (bb challenges / bb
  // flag) refuse anonymous users. Set from the template
  // (request.state.user), never guessed from the username: a real
  // account could literally be named "guest".
  var loggedIn = term.getAttribute('data-logged-in') === 'true';

  // --- virtual filesystem (home only; challenges live on the board) -----
  var HOME = '/home/guest';
  var FS = {
    '/': ['home'],
    '/home': ['guest'],
    '/home/guest': ['rules.txt', 'README.txt', 'flag.txt', 'challenges'],
    '/home/guest/challenges': []
  };
  var DIRS = { '/': true, '/home': true, '/home/guest': true, '/home/guest/challenges': true };
  var cwd = HOME;

  // v2.9: the old [02] rule marker is gone — its text is folded into
  // [01] (per-user flags are the whole point of this platform), the
  // rest renumbered down so the list stays a clean [01]-[10].
  var RULES = [
    '[01] Flag format is BB{...} — submit the exact flag, no extra spaces. Every player gets their own flag; sharing one never works.',
    '[02] No attacking the platform itself. No DoS, no login brute-force.',
    '[03] No flag sharing, and no asking other players for flags.',
    '[04] Solve it yourself — hints cost points, so use them wisely.',
    '[05] Respect the scope of each challenge; stay inside it.',
    '[06] Play fair — first blood earns glory.',
    '[07] Breaking the platform gets you banned.',
    '[08] One account per player. No sockpuppets, no shared accounts.',
    '[09] When in doubt, ask an admin — do not guess your way past a rule.',
    '[10] Sample flag (format reference only, not a real challenge flag): BB{2fd34a7795ef}',
  ];
  // v2.9: README is a text-only warm-up challenge — crypto + binary.
  // Layer 1: every letter Caesar-shifted by 12 (the twelve operators).
  // Layer 2: the shifted text written out in binary. The flag itself
  // is NEVER in this file — it lives only with the challenge author.
  var README = [
    'BYTE_BATALLION_CTF_PLATFORM — welcome, operator.',
    'This shell hides a warm-up challenge. No files, no links — just the text below.',
    'A message was scrambled in two layers: first every letter was shifted the same way,',
    'then the whole thing was written in binary.',
    'The shift is the number of operators in the Batallion. Twelve stand guard. Twelve steps.',
    'Decode it, and the flag is yours. Submit it as BB{...} on the board.',
    '',
    '01001110 01001110 01111011 01101111 01101101 00110011 01100101 01101101',
    '01100100 01011111 01111001 01110001 00110011 01100110 01100101 01011111',
    '01101110 00110001 01111010 01101101 01100100 01101011 01111101',
  ];
  var FILES = ['rules.txt', 'README.txt', 'flag.txt'];

  function shortPath(path) {
    if (path === HOME) return '~';
    if (path.indexOf(HOME + '/') === 0) return '~' + path.slice(HOME.length);
    return path;
  }

  function prompt() {
    return username + '@bytebatallion:' + shortPath(cwd) + '$';
  }

  function refreshPrompt() {
    promptEl.textContent = prompt();
  }

  function printLine(text) {
    var line = document.createElement('div');
    line.className = 'terminal-line-out';
    line.textContent = text;
    output.appendChild(line);
    output.scrollTop = output.scrollHeight; // output scrolls; scrollbars hidden by CSS
  }

  function printLines(lines) {
    lines.forEach(printLine);
  }

  function resolvePath(arg) {
    if (!arg || arg === '~') return HOME;
    var base = arg.charAt(0) === '/' ? '' : cwd;
    var parts = (base + '/' + arg).split('/');
    var out = [];
    parts.forEach(function (p) {
      if (!p || p === '.') return;
      if (p === '..') { out.pop(); return; }
      out.push(p);
    });
    return '/' + out.join('/');
  }

  function lsLong(names, dir) {
    var lines = ['total ' + names.length];
    lines.push('drwxr-xr-x  2 guest guest 4096 Nov  9 10:24 .');
    lines.push('drwxr-xr-x  3 guest guest 4096 Nov  9 10:24 ..');
    names.forEach(function (name) {
      var isDir = !!DIRS[(dir === '/' ? '' : dir) + '/' + name];
      var size = name === 'rules.txt' ? 628 : name === 'README.txt' ? 622 : name === 'flag.txt' ? 24 : 4096;
      lines.push((isDir ? 'drwxr-xr-x' : '-rw-r--r--') + '  1 guest guest ' + String(size).padStart(5, ' ') + ' Nov  9 10:24 ' + name);
    });
    return lines;
  }

  // --- command registry: add future challenge/flag commands here --------
  var COMMANDS = {
    help: {
      desc: 'show this list',
      run: function () {
        var lines = ['available commands:'];
        Object.keys(COMMANDS).forEach(function (name) {
          lines.push('  ' + name.padEnd(12, ' ') + COMMANDS[name].desc);
        });
        // v2.4: the `bb` line used to be hardcoded here too, so help
        // listed it TWICE — the registry entry above is the only one.
        lines.push('  Tab          complete command / file name');
        lines.push('  Ctrl+C       cancel the current line    Ctrl+L  clear screen');
        printLines(lines);
      }
    },
    ls: {
      desc: 'list files (ls -la for the long view)',
      run: function (args) {
        var flags = args.filter(function (a) { return a.charAt(0) === '-'; });
        var paths = args.filter(function (a) { return a.charAt(0) !== '-'; });
        var target = paths[0] ? resolvePath(paths[0]) : cwd;
        if (!FS[target]) {
          // ls on a single file just prints its name.
          var parent = target.split('/').slice(0, -1).join('/') || '/';
          var name = target.split('/').pop();
          if (FS[parent] && FS[parent].indexOf(name) !== -1) { printLine(name); return; }
          printLine('ls: cannot access \'' + (paths[0] || '') + '\': No such file or directory');
          return;
        }
        if (flags.indexOf('-la') !== -1 || flags.indexOf('-l') !== -1) {
          printLines(lsLong(FS[target], target));
        } else {
          printLine(FS[target].join('  ') || '(empty)');
          if (target === HOME + '/challenges') {
            printLine('// challenges live on the board — open /challenges after signing in.');
          }
        }
      }
    },
    pwd: {
      desc: 'print the working directory',
      run: function () { printLine(cwd); }
    },
    cd: {
      desc: 'change directory (cd, cd .., cd challenges)',
      run: function (args) {
        var target = resolvePath(args[0] || '~');
        if (!DIRS[target]) { printLine('cd: ' + (args[0] || '') + ': No such directory'); return; }
        cwd = target;
        refreshPrompt();
      }
    },
    cat: {
      desc: 'print a file (rules.txt, README.txt, flag.txt)',
      run: function (args) {
        if (!args[0]) { printLine('usage: cat <file>'); return; }
        var path = resolvePath(args[0]);
        var name = path.split('/').pop();
        if (path === HOME + '/rules.txt') printLines(RULES);
        else if (path === HOME + '/README.txt') printLines(README);
        // v2.9: flag.txt holds a simple base64-encoded flag — decode it
        // in the shell (or anywhere) to read it. It is a demo flag for
        // the author to build a challenge around, not a live solve.
        else if (path === HOME + '/flag.txt') printLine('QkJ7YmFzZTY0XzFzX2Vhc3l9');
        else if (DIRS[path]) printLine('cat: ' + args[0] + ': Is a directory');
        else printLine('cat: ' + args[0] + ': No such file');
      }
    },
    echo: {
      desc: 'print text back (echo hello world)',
      run: function (args) { printLine(args.join(' ')); }
    },
    whoami: {
      desc: 'print your username',
      run: function () { printLine(username); }
    },
    date: {
      desc: 'print the current date and time',
      run: function () { printLine(new Date().toString()); }
    },
    uname: {
      desc: 'print system info (uname -a)',
      run: function () { printLine('ByteBatallionOS bb-kernel 6.9.0-bb x86_64 GNU/Linux'); }
    },
    history: {
      desc: 'show the commands you ran in this shell',
      run: function () {
        if (!history.length) { printLine('(no commands yet)'); return; }
        history.forEach(function (cmd, i) { printLine('  ' + (i + 1) + '  ' + cmd); });
      }
    },
    clear: {
      desc: 'clear the terminal',
      run: function () { output.innerHTML = ''; }
    },
    sudo: {
      desc: 'try to become root',
      run: function () { printLine(username + ' is not in the sudoers file. This incident will be reported.'); }
    },
    exit: {
      desc: 'leave the shell',
      run: function () { printLine('there is no escape. type \'clear\' and keep hacking.'); }
    },
    bb: {
      desc: 'ByteBatallion platform shell (try: bb help)',
      run: function (args) {
        var sub = args[0] || '';
        if (sub === 'help') {
          printLines([
            'bb — ByteBatallion platform shell:',
            '  bb status      platform status for this session',
            '  bb challenges  where the challenges live (sign-in required)',
            '  bb flag <name> look up a challenge flag (sign-in required)'
          ]);
        } else if (sub === 'status') {
          printLine('platform: ONLINE | user: ' + username + ' | shell: v2.4 | flag format: BB{...} | ' + (loggedIn ? 'signed in' : 'not signed in'));
        } else if (sub === 'challenges' || sub === 'flag') {
          // v2.4 gate: the home page is public, so platform commands
          // refuse anonymous users instead of pretending to work. The
          // future server handler must enforce this gate again — a
          // client-side check alone never authorises anything.
          if (!loggedIn) {
            printLine('sign in to use platform commands — open /auth/login first.');
            return;
          }
          if (sub === 'challenges') {
            printLine('challenges live on the board: open /challenges.');
            printLine('a future bb handler can list them here once it calls the platform.');
          } else {
            printLine('flag lookup is not wired to the board yet — this is the hook:');
            printLine('add a handler in terminal.js COMMANDS that calls the platform');
            printLine('with a signed-in session. for now, submit flags on the challenge page.');
          }
        } else {
          printLine('usage: bb <help|status|challenges|flag>');
        }
      }
    }
  };

  var history = [];
  var histIdx = -1;

  function run(raw) {
    var cmdline = raw.trim();
    printLine(prompt() + ' ' + cmdline);
    if (!cmdline) return;
    history.push(cmdline);
    histIdx = history.length;
    var parts = cmdline.split(/\s+/);
    var cmd = parts[0];
    var args = parts.slice(1);
    if (COMMANDS[cmd]) {
      COMMANDS[cmd].run(args);
    } else {
      printLine("command not found: " + cmd + " — type 'help'.");
    }
  }

  // --- Tab completion: commands, then files/dirs in the current dir -----
  function complete() {
    var value = input.value;
    var before = value.slice(0, input.selectionStart || value.length);
    var words = before.split(/\s+/);
    var current = words[words.length - 1];
    var candidates = [];
    if (words.length === 1) {
      candidates = Object.keys(COMMANDS).filter(function (c) { return c.indexOf(current) === 0; });
    } else {
      candidates = (FS[cwd] || []).filter(function (f) { return f.indexOf(current) === 0; });
    }
    if (candidates.length === 1) {
      var completed = before.slice(0, before.length - current.length) + candidates[0];
      input.value = completed + value.slice(input.selectionStart || value.length);
      inputText.textContent = input.value;
    } else if (candidates.length > 1) {
      printLine(prompt() + ' ' + value);
      printLine(candidates.join('  '));
    }
  }

  refreshPrompt();

  input.addEventListener('input', function () {
    inputText.textContent = input.value;
  });

  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      run(input.value);
      input.value = '';
      inputText.textContent = '';
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (history.length && histIdx > 0) {
        histIdx -= 1;
        input.value = history[histIdx];
        inputText.textContent = input.value;
      }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (histIdx < history.length - 1) {
        histIdx += 1;
        input.value = history[histIdx];
      } else {
        histIdx = history.length;
        input.value = '';
      }
      inputText.textContent = input.value;
    } else if (e.key === 'Tab') {
      e.preventDefault();
      complete();
    } else if (e.ctrlKey && (e.key === 'c' || e.key === 'C')) {
      e.preventDefault();
      printLine(prompt() + ' ' + input.value + '^C');
      input.value = '';
      inputText.textContent = '';
    } else if (e.ctrlKey && (e.key === 'l' || e.key === 'L')) {
      e.preventDefault();
      output.innerHTML = '';
    }
  });

  term.addEventListener('click', function () {
    input.focus();
  });

  // Login banner, like a real shell.
  printLine('Last login: ' + new Date().toString() + ' from 127.0.0.1');
  printLine('ByteBatallion shell v2.4 — type \'help\' to list commands.');
  input.focus();
})();
