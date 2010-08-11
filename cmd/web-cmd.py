#!/usr/bin/env python
import sys, stat, cgi, shutil, urllib, mimetypes, posixpath, time
import tornado.httpserver
import tornado.ioloop
import tornado.template
import tornado.web
from bup import options, git, vfs
from bup.helpers import *

handle_ctrl_c()


def _compute_breadcrumbs(path):
    """Returns a list of breadcrumb objects for a path."""
    breadcrumbs = []
    breadcrumbs.append(('[root]', '/'))
    path_parts = path.split('/')[1:-1]
    full_path = '/'
    for part in path_parts:
        full_path += part + '/'
        breadcrumbs.append((part, full_path))
    return breadcrumbs


def _contains_hidden_files(n):
    """Return True if n contains files starting with a '.', False otherwise."""
    for sub in n:
        name = sub.name
        if len(name)>1 and name.startswith('.'):
            return True

    return False


def _compute_dir_contents(n, show_hidden=False):
    """Given a vfs node, returns an iterator for display info of all subs."""
    for sub in n:
        display = link = sub.name

        if not show_hidden and len(display)>1 and display.startswith('.'):
            continue

        # link should be based on fully resolved type to avoid extra
        # HTTP redirect.
        if stat.S_ISDIR(sub.try_resolve().mode):
            link = sub.name + "/"

        size = None
        if stat.S_ISDIR(sub.mode):
            display = sub.name + '/'
        elif stat.S_ISLNK(sub.mode):
            display = sub.name + '@'
        else:
            size = sub.size()

        yield (display, link, size)


class BupRequestHandler(tornado.web.RequestHandler):
    def get(self, path):
        return self._process_request(path)

    def head(self, path):
        return self._process_request(path)

    def _process_request(self, path):
        path = urllib.unquote(path)
        print 'Handling request for %s' % path
        try:
            n = top.resolve(path)
        except vfs.NoSuchFile:
            self.send_error(404)
            return
        f = None
        is_json = int(self.request.arguments.get('json', [0])[-1])
        if is_json:
            self._list_directory_json(path, n)
        elif stat.S_ISDIR(n.mode):
            self._list_directory(path, n)
        else:
            self._get_file(path, n)

    def _list_directory(self, path, n):
        """Helper to produce a directory listing.

        Return value is either a file object, or None (indicating an
        error).  In either case, the headers are sent.
        """
        if not path.endswith('/') and len(path) > 0:
            print 'Redirecting from %s to %s' % (path, path + '/')
            return self.redirect(path + '/', permanent=True)

        try:
            show_hidden = int(self.request.arguments.get('hidden', [0])[-1])
        except ValueError, e:
            show_hidden = False

        self.render(
            'list-directory.html',
            path=path,
            breadcrumbs=_compute_breadcrumbs(path),
            files_hidden=_contains_hidden_files(n),
            hidden_shown=show_hidden,
            dir_contents=_compute_dir_contents(n, show_hidden),
            # We need the standard url_escape so we don't escape /
            url_escape=urllib.quote)

    def _list_directory_json(self,path, n):
        """Helper to produce a json directory listing."""
        try:
            show_hidden = int(self.request.arguments.get('hidden', [0])[-1])
        except ValueError, e:
            show_hidden = False

        self.set_header("Content-Type", "application/json")
        json = '{"path":"%s","items":[' % path
        items = []
        directory_contents=_compute_dir_contents(n, show_hidden)
        for (name, item_path, size) in directory_contents:
            item = '{"name":"%s","path":"%s","size":"%s"},' % (name, path + item_path, size)
            items.append(item)
        json += ",".join(items)
        json += '}'

    def _get_file(self, path, n):
        """Process a request on a file.

        Return value is either a file object, or None (indicating an error).
        In either case, the headers are sent.
        """
        ctype = self._guess_type(path)

        self.set_header("Last-Modified", self.date_time_string(n.mtime))
        self.set_header("Content-Type", ctype)
        size = n.size()
        self.set_header("Content-Length", str(size))

        if self.request.method != 'HEAD':
            f = n.open()
            for blob in chunkyreader(f):
                self.write(blob)
            f.close()

    def _guess_type(self, path):
        """Guess the type of a file.

        Argument is a PATH (a filename).

        Return value is a string of the form type/subtype,
        usable for a MIME Content-type header.

        The default implementation looks the file's extension
        up in the table self.extensions_map, using application/octet-stream
        as a default; however it would be permissible (if
        slow) to look inside the data to make a better guess.
        """
        base, ext = posixpath.splitext(path)
        if ext in self.extensions_map:
            return self.extensions_map[ext]
        ext = ext.lower()
        if ext in self.extensions_map:
            return self.extensions_map[ext]
        else:
            return self.extensions_map['']

    if not mimetypes.inited:
        mimetypes.init() # try to read system mime.types
    extensions_map = mimetypes.types_map.copy()
    extensions_map.update({
        '': 'text/plain', # Default
        '.py': 'text/plain',
        '.c': 'text/plain',
        '.h': 'text/plain',
        })

    def date_time_string(self, t):
        return time.strftime('%a, %d %b %Y %H:%M:%S', time.gmtime(t))


optspec = """
bup web [[hostname]:port]
--
"""
o = options.Options('bup web', optspec)
(opt, flags, extra) = o.parse(sys.argv[1:])

if len(extra) > 1:
    o.fatal("at most one argument expected")

address = ('127.0.0.1', 8080)
if len(extra) > 0:
    addressl = extra[0].split(':', 1)
    addressl[1] = int(addressl[1])
    address = tuple(addressl)

git.check_repo_or_die()
top = vfs.RefList(None)

settings = dict(
    debug = 1,
    template_path = resource_path('web'),
    static_path = resource_path('web/src'),
)

# Disable buffering on stdout, for debug messages
sys.stdout = os.fdopen(sys.stdout.fileno(), 'w', 0)

application = tornado.web.Application([
    (r"(/.*)", BupRequestHandler),
], **settings)

if __name__ == "__main__":
    http_server = tornado.httpserver.HTTPServer(application)
    http_server.listen(address[1], address=address[0])

    print "Serving HTTP on %s:%d..." % http_server._socket.getsockname()
    loop = tornado.ioloop.IOLoop.instance()
    loop.start()

