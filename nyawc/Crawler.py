# -*- coding: utf-8 -*-

# MIT License
#
# Copyright (c) 2017 Tijme Gommers
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import sys
import time
import signal
import threading

from nyawc.Queue import Queue
from nyawc.QueueItem import QueueItem
from nyawc.Intelligence import Intelligence
from nyawc.CrawlerThread import CrawlerThread
from nyawc.CrawlerActions import CrawlerActions
from nyawc.helpers.HTTPRequestHelper import HTTPRequestHelper

class Crawler(object):
    """The main Crawler class which handles the crawling recursion, queue and processes.

    Attributes:
        queue (:class:`nyawc.Queue`): The request/response pair queue containing everything to crawl.
        __options (:class:`nyawc.Options`): The options to use for the current crawling runtime.
        __should_stop (bool): If the crawler should stop the crawling process.
        __stopping (bool): If the crawler is stopping the crawling process.
        __stopped (bool): If the crawler finished stopping the crawler process.
        __threads (obj): All currently running threads, as queue item hash => :class:`nyawc.CrawlerThread`.
        __lock (obj): The callback lock to prevent race conditions.
        __intelligence (:class:`nyawc.Intelligence`): The machine learning process manager.

    """

    def __init__(self, options):
        """Constructs a Crawler instance.

        Args:
            options (:class:`nyawc.Options`): The options to use for the current crawling runtime.

        """

        signal.signal(signal.SIGINT, self.__signal_handler)

        self.queue = Queue(options)
        self.__options = options
        self.__should_stop = False
        self.__stopping = False
        self.__stopped = False
        self.__threads = {}
        self.__lock = threading.Lock()

        if options.scope.ignore_similar_requests:
            self.__intelligence = Intelligence()
        else:
            self.__intelligence = None

    def __signal_handler(self, signum, frame):
        """On sigint (e.g. CTRL+C) stop the crawler.

        Args:
            signum (int): The signal number.
            frame (obj): The current stack frame.

        """

        self.__crawler_stop()

    def start_with(self, request):
        """Start the crawler using the given request.

        Args:
            request (:class:`nyawc.http.Request`): The startpoint for the crawler.

        """

        HTTPRequestHelper.patch_with_options(request, self.__options)
        self.queue.add_request(request)

        self.__crawler_start()

    def __spawn_new_requests(self):
        """Spawn new requests until the max threads option value is reached.

        Note:
            If no new requests were spawned and there are no requests in progress
            the crawler will stop crawling.

        """

        in_progress_count = len(self.queue.get_all(QueueItem.STATUS_IN_PROGRESS))

        while in_progress_count < self.__options.performance.max_threads:
            if self.__spawn_new_request():
                in_progress_count += 1
            else:
                break

        if in_progress_count == 0:
            self.__crawler_stop()

    def __spawn_new_request(self):
        """Spawn the first queued request if there is one available.

        Returns:
            bool: True if a new request was spawned, false otherwise.

        """

        first_in_line = self.queue.get_first(QueueItem.STATUS_QUEUED)
        if first_in_line is None:
            return False

        self.__request_start(first_in_line)
        return True

    def __wait_for_current_threads(self):
        """Wait until all the current threads are finished."""

        for thread in list(self.__threads.values()):
            thread.join()

    def __crawler_start(self):
        """Spawn the first X queued request, where X is the max threads option.

        Note:
            The main thread will sleep until the crawler is finished. This enables
            quiting the application using sigints (see http://stackoverflow.com/a/11816038/2491049).

        """

        try:
            self.__options.callbacks.crawler_before_start()
        except Exception as e:
            print(e)

        self.__spawn_new_requests()

        while not self.__stopped:
            if self.__should_stop:
                self.__crawler_stop()

            time.sleep(1)

    def __crawler_stop(self):
        """Mark the crawler as stopped.

        Note:
            If :attr:`__stopped` is True, the main thread will be stopped. Every piece of code that gets
            executed after :attr:`__stopped` is True could cause Thread exceptions and or race conditions.

        """

        if self.__stopping:
            return

        self.__stopping = True
        self.__wait_for_current_threads()

        self.queue.move_bulk([
            QueueItem.STATUS_QUEUED,
            QueueItem.STATUS_IN_PROGRESS
        ], QueueItem.STATUS_CANCELLED)

        self.__crawler_finish()
        self.__stopped = True

    def __crawler_finish(self):
        """Called when the crawler is finished because there are no queued requests left or it was stopped."""

        try:
            self.__options.callbacks.crawler_after_finish(self.queue)
        except Exception as e:
            print(e)

    def __request_start(self, queue_item):
        """Execute the request in given queue item.

        Args:
            queue_item (:class:`nyawc.QueueItem`): The request/response pair to scrape.

        """

        try:
            action = self.__options.callbacks.request_before_start(self.queue, queue_item)
        except Exception as e:
            action = None
            print(e)

        if action == CrawlerActions.DO_STOP_CRAWLING:
            self.__should_stop = True

        if action == CrawlerActions.DO_SKIP_TO_NEXT:
            self.queue.move(queue_item, QueueItem.STATUS_FINISHED)

        if action == CrawlerActions.DO_CONTINUE_CRAWLING or action is None:
            self.queue.move(queue_item, QueueItem.STATUS_IN_PROGRESS)

            thread = CrawlerThread(self.__request_finish, self.__lock, self.__options, queue_item)
            self.__threads[queue_item.get_hash()] = thread
            thread.daemon = True
            thread.start()

    def __request_finish(self, queue_item, new_requests, request_failed=False):
        """Called when the crawler finished the given queue item.

        Args:
            queue_item (:class:`nyawc.QueueItem`): The request/response pair that finished.
            new_requests list(:class:`nyawc.http.Request`): All the requests that were found during this request.
            request_failed (bool): True if the request failed (if needs to be moved to errored).

        """

        if self.__stopping:
            return

        del self.__threads[queue_item.get_hash()]

        if request_failed:
            new_queue_items = []
            self.queue.move(queue_item, QueueItem.STATUS_ERRORED)
        else:
            new_queue_items = self.__add_scraped_requests_to_queue(queue_item, new_requests)
            self.queue.move(queue_item, QueueItem.STATUS_FINISHED)

        try:
            action = self.__options.callbacks.request_after_finish(self.queue, queue_item, new_queue_items)
        except Exception as e:
            action = None
            print(e)

        if action == CrawlerActions.DO_STOP_CRAWLING:
            self.__should_stop = True

        if action == CrawlerActions.DO_CONTINUE_CRAWLING or action is None:
            self.__spawn_new_requests()

    def __add_scraped_requests_to_queue(self, queue_item, scraped_requests):
        """Convert the scraped requests to queue items, return them and also add them to the queue.

        Args:
            queue_item (:class:`nyawc.QueueItem`): The request/response pair that finished.
            new_requests list(:class:`nyawc.http.Request`): All the requests that were found during this request.

        Returns:
            list(:class:`nyawc.QueueItem`): The new queue items.

        """

        new_queue_items = []

        for scraped_request in scraped_requests:
            HTTPRequestHelper.patch_with_options(scraped_request, self.__options, queue_item)

            if not HTTPRequestHelper.complies_with_scope(queue_item, scraped_request, self.__options.scope, self.__intelligence):
                continue

            if self.queue.has_request(scraped_request):
                continue

            scraped_request.depth = queue_item.request.depth + 1
            if self.__options.scope.max_depth is not None:
                if scraped_request.depth > self.__options.scope.max_depth:
                    continue

            new_queue_item = self.queue.add_request(scraped_request)
            new_queue_items.append(new_queue_item)

        return new_queue_items
