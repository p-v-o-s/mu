import os
import time
import logging
import ast
from mu.modes.base import MicroPythonMode
#from mu.contrib.upython_device import SerialuPythonDevice
from mu.modes.api import ADAFRUIT_APIS, SHARED_APIS
from mu.interface.panes import CHARTS
from PyQt5.QtCore import QObject, QThread, pyqtSignal, QTimer

from serial import Serial

logger = logging.getLogger(__name__)

# customize some methods to work better for ESP devices
class SerialuPythonDevice:
    def __init__(self, port, baudrate=115200):
        print(port)
        self.serial = Serial(port,baudrate=baudrate)
        time.sleep(0.1)
        self.serial.write(b'\x02')  # Send Ctrl-B to ensure not raw mode
        self.serial.write(b'\x03')  # Send a Control-C
        self.serial.write(b'\r\n')  # Send a Control-C
        
    def list_files(self):
        """
        Returns a list of the files on the connected device or raises an IOError
        if there's a problem.
        """
        out, err = self.execute_commands([
            'import os',
            'print(os.listdir())',
        ])
        if err:
            raise IOError(err)
        return ast.literal_eval(out.decode('utf-8'))
        
    def put_file(self, local_path, remote_filename=None):
        """
        Puts a referenced file on the LOCAL file system onto the
        file system on the remote device.

        Returns True for success or raises an IOError if there's a problem.
        """
        if not os.path.isfile(local_path):
            raise IOError('No such file.')
        with open(local_path, 'rb') as local:
            content = local.read()
        if remote_filename is None:
            remote_filename = os.path.basename(local_path)
        commands = [
            "fd = open('{}', 'wb')".format(remote_filename),
            "w = fd.write",
        ]
        while content:
            line = content[:64]
            commands.append('w(' + repr(line) + ')')
            content = content[64:]
        commands.append('fd.close()')
        out, err = self.execute_commands(commands)
        if err:
            raise IOError(err)
        return True

    def get_file(self, remote_filename, local_path=None):
        """
        Gets a referenced file on the device's file system and copies it to the
        target (or current working directory if unspecified).

        Returns True for success or raises an IOError if there's a problem.
        """
        if local_path is None:
            local_path = remote_filename
        commands = [  # TODO - should ensure ESP OS debugging is off
            "import sys",
            "f = open('{}', 'rb')".format(remote_filename),
            "r = f.read",
            "w = sys.stdout.buffer.write",          #write binary data to stdout
            "result = True",
            "while result:\n    result = r(32)\n    if result:\n" # cont below
            "       w(result)\n",
            #"while f.read(32): print(_, end='')\n",
            "f.close()",
        ]
        out, err = self.execute_commands(commands)
        if err:
            raise IOError(err)
        # Recombine the bytes while removing "b'" from start and "'" from end.
        #print(local_path)
        with open(local_path, 'wb') as f:
            f.write(out)
        return True

    def del_file(self, remote_filename):
        """
        Removes a referenced file on the uPython device.

        Returns True for success or raises an IOError if there's a problem.
        """
        commands = [
            "import os",
            "os.remove('{}')".format(remote_filename),
        ]
        out, err = self.execute_commands(commands)
        if err:
            raise IOError(err)
        return True
        
    def execute_commands(self, commands):
        """
        executes the commands in the list `commands` on the device via the REPL

        For this to work correctly, a particular sequence of commands needs to
        be sent to put the device into a good state to process the incoming
        command.

        Returns the stdout and stderr output from the uPython device.
        """
        result = b''
        self.raw_on()
        # Write the actual command and send CTRL-D to evaluate.
        for command in commands:
            command_bytes = command.encode('utf-8')
            for i in range(0, len(command_bytes), 32):
                self.send(command_bytes[i:min(i + 32, len(command_bytes))])
                time.sleep(0.01)
            self.send(b'\x04')
            response = bytearray()
            while not response.endswith(b'\x04>'):  # Read until prompt.
                response.extend(self.read_all())
            out, err = response[2:-2].split(b'\x04', 1)  # Split stdout, stderr
            #print(out)
            result += out
            if err:
                return b'', err
        self.raw_off()
        return result, err
        
    def send(self, bs):
        return self.serial.write(bs)  # serial.write takes a byte array
        
    def read(self, count):
        data = self.serial.read(count)
        return data
        
    def read_all(self):
        data = self.serial.read_all()
        return data
        
    def read_until(self, terminator=b'\n', size=None):
        """
        Read until a termination sequence is found ('\n' by default), the size
        is exceeded or until timeout occurs.
        """
        lenterm = len(terminator)
        line = bytearray()
        while True:
            c = self.read(1)
            if c:
                line += c
                if line[-lenterm:] == terminator:
                    break
                if size is not None and len(line) >= size:
                    break
            else:
                break
        return bytes(line)
        
    def raw_on(self):
        """
        Puts the device into raw mode.
        """
        print("uPythonDevice.raw_on")
        # Flush input (without relying on serial.flushInput())
        self.read_all()
        # Send CTRL-B to end raw mode if required.
        self.send(b'\x02')
        # Send CTRL-C three times between pauses to break out of loop.
        for i in range(3):
            self.send(b'\r\x03')
            time.sleep(0.01)
        # Go into raw mode with CTRL-A.
        self.send(b'\r\x01')
        # Flush
        data = self.read_until(b'raw REPL; CTRL-B to exit\r\n>')
        if not data.endswith(b'raw REPL; CTRL-B to exit\r\n>'):
            print(data)
            raise IOError('Could not enter raw REPL.')
        # Soft Reset with CTRL-D
        self.send(b'\x04')
        data = self.read_until(b'soft reboot\r\n')
        if not data.endswith(b'soft reboot\r\n'):
            print(data)
            raise IOError('Could not enter raw REPL.')
        self.send(b'\r\n')#send return to enter RAW REPL CWV
        data = self.read_until(b'raw REPL; CTRL-B to exit\r\n>')
        if not data.endswith(b'raw REPL; CTRL-B to exit\r\n>'):
            print(data)
            raise IOError('Could not enter raw REPL.')
            
    def raw_off(self):
        """ Takes the device out of raw mode. """
        self.send(b'\x02')  # Send CTRL-B to get out of raw mode.

class ESPSerialuPythonDevice(SerialuPythonDevice):
    def __init__(self, port, baudrate = 115200):
        SerialuPythonDevice.__init__(self, port, baudrate = baudrate)
        #important turn OS debugging messages off!
        self.serial.write(b'import esp;esp.osdebug(None)\r\n')


class FileManager(QObject):
    """
    Used to manage uPython device filesystem operations in a manner such that the
    UI remains responsive.

    Provides an FTP-ish API. Emits signals on success or failure of different
    operations.
    """

    # Emitted when the tuple of files on the uPython device is known.
    on_list_files = pyqtSignal(tuple)
    # Emitted when the file with referenced filename is got from the uPython device.
    on_get_file = pyqtSignal(str)
    # Emitted when the file with referenced filename is put onto the uPython device.
    on_put_file = pyqtSignal(str)
    # Emitted when the file with referenced filename is deleted from the
    # uPython device.
    on_delete_file = pyqtSignal(str)
    # Emitted when Mu is unable to list the files on the uPython device.
    on_list_fail = pyqtSignal()
    # Emitted when the referenced file fails to be got from the uPython device.
    on_get_fail = pyqtSignal(str)
    # Emitted when the referenced file fails to be put onto the uPython device.
    on_put_fail = pyqtSignal(str)
    # Emitted when the referenced file fails to be deleted from the uPython device.
    on_delete_fail = pyqtSignal(str)
    
    port = None
    baudrate = None
    
    def on_start(self):
        """
        Run when the thread containing this object's instance is started so
        it can emit the list of files found on the connected uPython device.
        """
        self.upydev = ESPSerialuPythonDevice(self.port_path, baudrate=self.baudrate)
        self.ls()

    def ls(self):
        """
        List the files on the uPython device. Emit the resulting tuple of filenames
        or emit a failure signal.
        """
        print("esp.FileManager.ls")
        try:
            result = tuple(self.upydev.list_files())
            self.on_list_files.emit(result)
        except Exception as ex:
            import traceback
            traceback.print_exc(ex)
            logger.exception(ex)
            self.on_list_fail.emit()

    def get(self, remote_filename, local_filename):
        """
        Get the referenced uPython device filename and save it to the local
        filename. Emit the name of the filename when complete or emit a
        failure signal.
        """
        try:
            self.upydev.get_file(remote_filename,local_filename)
            self.on_get_file.emit(remote_filename)
        except Exception as ex:
            logger.error(ex)
            self.on_get_fail.emit(remote_filename)

    def put(self, local_filename):
        """
        Put the referenced local file onto the filesystem on the uPython device.
        Emit the name of the file on the uPython device when complete, or emit
        a failure signal.
        """
        try:
            self.upydev.put_file(local_filename)
            self.on_put_file.emit(os.path.basename(local_filename))
        except Exception as ex:
            logger.error(ex)
            self.on_put_fail.emit(local_filename)

    def delete(self, remote_filename):
        """
        Delete the referenced file on the uPython device's filesystem. Emit the name
        of the file when complete, or emit a failure signal.
        """
        try:
            microfs.rm(remote_filename)
            self.on_delete_file.emit(remote_filename)
        except Exception as ex:
            logger.error(ex)
            self.on_delete_fail.emit(remote_filename)

class ESPMode(MicroPythonMode):
    name = _('ESP')
    description = _("Write MicroPython for the ESP8266 or ESP32.")
    icon = 'esp'
    fs = None  #: Reference to filesystem navigator.
    valid_boards = [
        (0x10C4, 0xEA60),  # Cygnal Integrated Products, Inc. CP210x UART Bridge
    ]
    baudrate = 115200
    
    def actions(self):
        """
        Return an ordered list of actions provided by this module. An action
        is a name (also used to identify the icon) , description, and handler.
        """
        buttons = [
            {
                'name': 'serial',
                'display_name': _('Serial'),
                'description': _('Open a serial connection to your device.'),
                'handler': self.toggle_repl,
                'shortcut': 'CTRL+Shift+S',
            }, 
            {
                'name': 'files',
                'display_name': _('Files'),
                'description': _('Access the file system on the esp.'),
                'handler': self.toggle_files,
                'shortcut': 'F4',
            },
            ]
        if CHARTS:
            buttons.append({
                'name': 'plotter',
                'display_name': _('Plotter'),
                'description': _('Plot incoming REPL data.'),
                'handler': self.toggle_plotter,
                'shortcut': 'CTRL+Shift+P',
            })
        return buttons
        
    def toggle_files(self, event):
        """
        Check for the existence of the REPL or plotter before toggling the file
        system navigator for the esp8266 on or off.
        """
        if (self.repl or self.plotter):
            message = _("File system cannot work at the same time as the "
                        "REPL or plotter.")
            information = _("The file system and the REPL and plotter "
                            "use the same USB serial connection. Toggle the "
                            "REPL and plotter off and try again.")
            self.view.show_message(message, information)
        else:
            if self.fs is None:
                self.add_fs()
                if self.fs:
                    logger.info('Toggle filesystem on.')
                    self.set_buttons(repl=False, plotter=False)
            else:
                self.remove_fs()
                logger.info('Toggle filesystem off.')
                self.set_buttons(repl=True, plotter=True)

    def add_fs(self):
        """
        Add the file system navigator to the UI.
        """
        # Check for USB serial device
        port_path = self.find_device()
        if port_path is None:
            message = _('Could not find an attached ESP device.')
            information = _("Please make sure the device is plugged "
                            "into this computer.\n\nThe device must "
                            "have MicroPython flashed onto it before "
                            "the file system will work.\n\n"
                            "Finally, press the device's reset button "
                            "and wait a few seconds before trying "
                            "again.")
            self.view.show_message(message, information)
            return
        self.file_manager_thread = QThread(self)
        self.file_manager = FileManager()
        self.file_manager.port_path = port_path
        self.file_manager.baudrate  = self.baudrate

        self.file_manager.moveToThread(self.file_manager_thread)
        self.file_manager_thread.started.\
            connect(self.file_manager.on_start)
        self.fs = self.view.add_filesystem(self.workspace_dir(),
                                           self.file_manager)
        self.fs.set_message.connect(self.editor.show_status_message)
        self.fs.set_warning.connect(self.view.show_message)
        self.file_manager_thread.start()

    def remove_fs(self):
        """
        Remove the file system navigator from the UI.
        """
        if self.fs is None:
            raise RuntimeError("File system not running")
        self.view.remove_filesystem()
        self.file_manager = None
        self.file_manager_thread = None
        self.fs = None
        
    def api(self):
        """
        Return a list of API specifications to be used by auto-suggest and call
        tips.
        """
        return SHARED_APIS + ADAFRUIT_APIS
    
