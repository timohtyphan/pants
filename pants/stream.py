###############################################################################
#
# Copyright 2011 Chris Davis
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
###############################################################################

###############################################################################
# Imports
###############################################################################

import socket

from pants.channel import Channel
from pants.engine import Engine


###############################################################################
# Logging
###############################################################################

import logging
log = logging.getLogger("pants")


###############################################################################
# Stream Class
###############################################################################

class Stream(Channel):
    """
    """
    def __init__(self, **kwargs):
        if "type" not in kwargs:
            kwargs["type"] = socket.SOCK_STREAM
        
        Channel.__init__(self, **kwargs)
        
        # Internal state
        self._connected = False
        self._connecting = False
        self._listening = False
    
    ##### Status Methods ######################################################
    
    def active(self):
        """
        Returns True if the stream is not closed and is either connected
        or listening.
        """
        return not self.closed() and (self._listening or self._connected or self._connecting)
    
    def connected(self):
        """
        Returns True if the stream is connected to a remote socket.
        """
        return self._connected or self._connecting
    
    def listening(self):
        """
        Returns True if the stream is listening for connections.
        """
        return self._listening
    
    def closed(self):
        """
        Returns True if the stream is closed.
        """
        return self._socket is None
    
    ##### Control Methods #####################################################
    
    def connect(self, host, port):
        """
        Connects the stream to a remote socket.
        """
        if self.active():
            # TODO Should this raise an exception?
            log.warning("connect() called on active %s #%d."
                    % (self.__class__.__name__, self.fileno))
            return self
        
        self._connecting = True
        
        try:
            connected = self._socket_connect((host, port))
        except socket.error, err:    
            # TODO Raise exception here?
            log.exception("Exception raised in connect() on %s #%d." %
                    (self.__class__.__name__, self.fileno))
            # TODO Close this Stream here?
            self.close()
            return self
        
        if connected:
            self._handle_connect_event()
        
        return self
    
    def listen(self, port=8080, host='', backlog=1024):
        """
        Begins listening for connections made to the stream.
        """
        if self.active():
            # TODO Should this raise an exception?
            log.warning("listen() called on active %s #%d."
                    % (self.__class__.__name__, self.fileno))
            return self
        
        try:
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass
        
        try:
            self._socket_bind((host, port))
            self._socket_listen(backlog)
        except socket.error, err:    
            # TODO Raise exception here?
            log.exception("Exception raised in listen() on %s #%d." %
                    (self.__class__.__name__, self.fileno))
            # TODO Close this Stream here?
            self.close()
            return self
        
        self._update_addr()
        self._listening = True
        
        return self
    
    def close(self):
        """
        Closes the stream.
        """
        if self.closed():
            return
        
        Engine.instance().remove_channel(self)
        self._socket_close()
        self._connected = False
        self._connecting = False
        self._listening = False
        self._recv_buffer = ""
        self.read_delimiter = None
        self._send_buffer = ""
        self._update_addr()
        self._safely_call(self.on_close)
    
    def end(self):
        """
        Closes the stream after writing any pending data to the socket.
        """
        if self.closed():
            return
        
        if not self._send_buffer:
            self.close()
        else:
            self.on_write = self.close
    
    ##### I/O Methods #########################################################
    
    def write(self, data, buffer_data=False):
        """
        """
        self._send(data, buffer_data)
    
    def _send(self, data, buffer_data):
        """
        """
        if self.closed():
            log.warning("Attempted to write to closed %s #%d." %
                    (self.__class__.__name__, self.fileno))
            return
        
        if not self._connected:
            log.warning("Attempted to write to disconnected %s #%d." %
                    (self.__class__.__name__, self.fileno))
            return
        
        if buffer_data or self._send_buffer:
            self._send_buffer += data
            # NOTE _wait_for_write() is normally called by _socket_send()
            #      when no more data can be sent. We call it here because
            #      _socket_send() will not be called.
            self._wait_for_write()
            return
        
        try:
            bytes_sent = self._socket_send(data)
        except socket.error, err:
            # TODO Raise an exception here?
            log.exception("Exception raised in write() on %s #%d." %
                    (self.__class__.__name__, self.fileno))
            # TODO Close this Stream here?
            self.close()
            return
        
        if len(data[bytes_sent:]) > 0:
            self._send_buffer += data[bytes_sent:]
        else:
            self._safely_call(self.on_write)
    
    ##### Internal Methods ####################################################
    
    def _update_addr(self):
        if self._connected:
            self.remote_addr = self._socket.getpeername()
            self.local_addr = self._socket.getsockname()
        elif self._listening:  
            self.remote_addr = (None, None)
            self.local_addr = self._socket.getsockname()
        else:
            self.remote_addr = (None, None)
            self.local_addr = (None, None)
    
    ##### Internal Event Handler Methods ######################################
    
    def _handle_read_event(self):
        """
        Handles a read event raised on the Stream.
        """
        if self._listening:
            self._handle_accept_event()
            return
        
        while True:
            try:
                data = self._socket_recv()
            except socket.error, err:
                log.exception("Exception raised by recv() on %s #%d." %
                        (self.__class__.__name__, self.fileno))
                # TODO Close this Stream here?
                self.close()
                return
            
            if not data:
                break
            
            self._recv_buffer += data
        
        self._process_recv_buffer()
    
    def _handle_write_event(self):
        """
        Handles a write event raised on the Stream.
        """
        if self._listening:
            log.warning("Received write event for listening %s #%d." %
                    (self.__class__.__name__, self.fileno))
            return
        
        if not self._connected:
            self._handle_connect_event()
        
        if not self._send_buffer:
            return
        
        try:
            bytes_sent = self._socket_send(self._send_buffer)
        except socket.error, err:
            log.exception("Exception raised by send() on %s #%s." %
                    (self.__class__.__name__, self.fileno))
            # TODO Close this Stream here?
            self.close()
            return
        self._send_buffer = self._send_buffer[bytes_sent:]
        
        if not self._send_buffer:
            self._safely_call(self.on_write)
    
    def _handle_accept_event(self):
        """
        Handles an accept event raised on the Stream.
        """
        while True:
            try:
                sock, addr = self._socket_accept()
            except socket.error, err:
                log.exception("Exception raised by accept() on %s #%d." %
                        (self.__class__.__name__, self.fileno))
                try:
                    sock.close()
                except socket.error, err:
                    pass
                # TODO Close this Stream here?
                return
            
            if sock is None:
                return
            
            self._safely_call(self.on_accept, sock, addr)
    
    def _handle_connect_event(self):
        """
        Handles a connect event raised on the Stream.
        """
        err = self._socket.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if err != 0:
            errstr = "Unknown error %d" % err
            try:
                errstr = os.strerror(err)
            except (NameError, OverflowError, ValueError):
                if err in errno.errorcode:
                    errstr = errno.errorcode[err]
            
            raise socket.error(err, errstr)
        
        self._update_addr()
        self._connected = True
        self._connecting = False
        self._safely_call(self.on_connect)
    
    ##### Internal Processing Methods #########################################    
    
    def _process_recv_buffer(self):
        while self._recv_buffer:
            delimiter = self.read_delimiter
            
            if delimiter is None:
                data = self._recv_buffer
                self._recv_buffer = ""
                self._safely_call(self.on_read, data)
            
            elif isinstance(delimiter, (int, long)):
                if len(self._recv_buffer) < delimiter:
                    break
                data = self._recv_buffer[:delimiter]
                self._recv_buffer = self._recv_buffer[delimiter:]
                self._safely_call(self.on_read, data)
            
            elif isinstance(delimiter, basestring):
                mark = self._recv_buffer.find(delimiter)
                if mark == -1:
                    break
                data = self._recv_buffer[:mark]
                self._recv_buffer = self._recv_buffer[mark+len(delimiter):]
                self._safely_call(self.on_read, data)
            
            else:
                log.warning("Invalid read_delimiter on %s #%d." %
                        (self.__class__.__name__, self.fileno))
                break
            
            if self.closed() or not self._connected:
                break