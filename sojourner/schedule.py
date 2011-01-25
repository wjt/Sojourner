# vim: set fileencoding=utf-8 sts=4 sw=4 :

import xml.dom.minidom as minidom
from xml.dom.minidom import Node
from xml.parsers.expat import ExpatError
from datetime import datetime
import cPickle
import os.path

from sojourner.malvern import config_file, esc

_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday',
         'Saturday', 'Sunday']

def getChildrenByTagName(node, name):
    """Similar to node.getElementsByTagName(name), but only fetches immediate
    children."""
    return [child for child in node.childNodes if child.nodeName == name]

def get_text(node, strip_newlines=False):
    """Concatenates all of node's text children, optionally removing single
    newlines (but preserving paragraphs)."""
    text = ''.join([child.data for child in node.childNodes
                               if child.nodeType == Node.TEXT_NODE])
    if strip_newlines:
        # The schedule has a bunch of places which do this:
        #   "paragraph one\n\nparagraph two"
        # and some that do this:
        #   "paragraph one\n \nparagraph two"
        # This is tediously ad-hoc, and a real Markdown parser would be better.
        tidier_double_newlines = '\n'.join(text.split(' \n'))
        return '\n\n'.join(
            [p.replace('\n', ' ')
                for p in tidier_double_newlines.split('\n\n')])
    else:
        return text

def get_text_from_children(parent, name, joiner=''):
    """Given a node, returns the text contents of all its children named
    'name', joined by 'joiner'. For example, given a node 'foo' representing
    this stanza:

        <foo>
          <bar>hello</bar>
          <baz>not this one</baz>
          <bar>world</bar>
        <foo>

    then:

        >>> get_text_from_children(foo, 'bar', joiner=' ')
        u'hello world'.
    """

    texts = [get_text(c) for c in getChildrenByTagName(parent, name)]
    return joiner.join(texts)

def calculate_end(start, duration):
    h1, m1 = start.split(':')
    h2, m2 = duration.split(':')

    h3 = int(h1) + int(h2)
    m3 = int(m1) + int(m2)

    h4 = h3 + (m3 / 60)
    m4 = m3 % 60

    return "%02d:%02d" % (h4, m4)

def by_date_time(x, y):
    a = cmp(x.date, y.date)
    if a != 0:
        return a
    else:
        return cmp(x.start, y.start)

class MalformedSchedule(Exception):
    pass

class Schedule(object):
    """Version number for pickled event data. This must be incremented if this
    class, or Event, is modified."""
    __VERSION = 3

    def __init__(self, schedule_path):
        self.schedule_path = schedule_path

        (self.events, self.events_by_id, self.events_by_room,
            self.events_by_track) = self.__load_schedule()

        self.favourites = self.__load_favourites()

    def __load_schedule(self):
        """Tries to load the schedule from a pre-parsed pickle file; if that
        doesn't fly, reads the actual XML and pickles the result for later."""
        pickle_path = self.schedule_path + '.pickle'

        try:
            if os.path.getmtime(pickle_path) <= \
                    os.path.getmtime(self.schedule_path):
                raise Exception('pickle is out of date')

            version, stuff = cPickle.load(open(pickle_path, 'rb'))

            if version != Schedule.__VERSION:
                raise Exception('expected version %u, got version %u' %
                    (Schedule.__VERSION, version))

            return stuff
        except Exception, e:
            stuff = self.__parse_schedule()

            try:
                cPickle.dump((Schedule.__VERSION, stuff),
                    open(pickle_path, 'wb'),
                    protocol=2)
            except Exception, e:
                print "Couldn't pickle schedule: %s" % e

            return stuff

    def __parse_schedule(self):
        try:
            doc = minidom.parse(self.schedule_path)
        except ExpatError, e:
            raise MalformedSchedule(e)

        schedule_elt = doc.documentElement

        if doc.documentElement.nodeName != 'schedule':
            raise MalformedSchedule('Root element was <%s/>, not <schedule/>' %
                doc.documentElement.nodeName)

        events = []
        events_by_id = {}
        events_by_room = {}
        events_by_track = {}

        for day in getChildrenByTagName(doc.documentElement, 'day'):
            date = datetime.strptime(day.getAttribute('date'), '%Y-%m-%d')
            day_name = _DAYS[date.weekday()]

            for room_node in getChildrenByTagName(day, 'room'):
                room = room_node.getAttribute('name')

                for node in getChildrenByTagName(room_node, 'event'):
                    e = Event(node, day_name, room)
                    events.append(e)
                    events_by_id[e.id] = e

                    blah = events_by_room.get(e.room, [])
                    blah.append(e)
                    events_by_room[e.room] = blah

                    blah = events_by_track.get(e.track, [])
                    blah.append(e)
                    events_by_track[e.track] = blah

        events.sort(cmp=by_date_time)

        return (events, events_by_id, events_by_room, events_by_track)

    def __load_favourites(self):
        favourites = []

        try:
            f = file(self._favourites_file(), 'r')
            for id in f.readlines():
                favourites.append(self.events_by_id[id.strip()])
            f.close()
        except IOError:
            # I guess they don't have any favourites
            pass

        return favourites

    def _favourites_file(self):
        return os.path.dirname(self.schedule_path) + '/favourites'

    def _write_favourites(self):
        f = file(self._favourites_file(), 'w')
        for fav in self.favourites:
            f.write("%s\n" % fav.id)
        f.close()

    def add_favourite(self, event):
        self.favourites.append(event)
        self.favourites.sort(cmp=by_date_time)
        self._write_favourites()

    def remove_favourite(self, event):
        self.favourites.remove(event)
        self._write_favourites()

class Event(object):
    def __init__(self, node, date, room):
        self.date = date
        self.id = node.getAttribute('id')
        self.room = room

        children = [ c for c in node.childNodes
                       if c.nodeType == Node.ELEMENT_NODE
                   ]
        for child in children:
            n = child.nodeName

            if n == 'title':
                self.title = get_text(child)
            elif n == 'start':
                self.start = get_text(child)
            elif n == 'duration':
                self.duration = get_text(child)
            elif n == 'track':
                self.track = get_text(child)

            # In practice, abstract and description are the only places that
            # stray newlines show up. FIXME: I think they're actually in
            # Markdown format, maybe we could use Python-Markdown to do better
            # than this?
            elif n == 'abstract':
                self.abstract = get_text(child, strip_newlines=True)
            elif n == 'description':
                self.description = get_text(child, strip_newlines=True)
            elif n == 'persons':
                # FIXME: maybe joining the people together should be up to the
                # widgets?
                self.person = get_text_from_children(child, 'person',
                    joiner=', ')
            else:
                pass

        self.end = calculate_end(self.start, self.duration)

    def summary(self):
        return "<b>%s</b>\n<small>%s <i>(%s, %s–%s, %s, %s track)</i></small>" \
            % (esc(self.title),
               esc(self.person),
               esc(self.date), esc(self.start), esc(self.end),
               esc(self.room), esc(self.track))

    def full(self):
        if self.description.startswith(self.abstract):
            desc = self.description[len(self.abstract):]
        else:
            desc = self.description

        if desc == '':
            return "%s\n\n%s" % (self.summary(), esc(self.abstract))
        elif self.abstract == '':
            return "%s\n\n%s" % (self.summary(), esc(desc))
        else:
            return "%s\n\n%s\n\n%s" \
                % (self.summary(), esc(self.abstract), esc(desc))
