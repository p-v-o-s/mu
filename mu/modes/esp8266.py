import logging
from mu.modes.base import MicroPythonMode
from mu.contrib import uflash, microfs
from mu.modes.api import ADAFRUIT_APIS, SHARED_APIS
from mu.interface.panes import CHARTS
from PyQt5.QtCore import QObject, QThread, pyqtSignal, QTimer

logger = logging.getLogger(__name__)

class FileManager(QObject):
    """
    Used to manage micro:bit filesystem operations in a manner such that the
    UI remains responsive.

    Provides an FTP-ish API. Emits signals on success or failure of different
    operations.
    """

    # Emitted when the tuple of files on the micro:bit is known.
    on_list_files = pyqtSignal(tuple)
    # Emitted when the file with referenced filename is got from the micro:bit.
    on_get_file = pyqtSignal(str)
    # Emitted when the file with referenced filename is put onto the micro:bit.
    on_put_file = pyqtSignal(str)
    # Emitted when the file with referenced filename is deleted from the
    # micro:bit.
    on_delete_file = pyqtSignal(str)
    # Emitted when Mu is unable to list the files on the micro:bit.
    on_list_fail = pyqtSignal()
    # Emitted when the referenced file fails to be got from the micro:bit.
    on_get_fail = pyqtSignal(str)
    # Emitted when the referenced file fails to be put onto the micro:bit.
    on_put_fail = pyqtSignal(str)
    # Emitted when the referenced file fails to be deleted from the micro:bit.
    on_delete_fail = pyqtSignal(str)
    
    vid    = None
    pid    = None
    serial = None

    def on_start(self):
        """
        Run when the thread containing this object's instance is started so
        it can emit the list of files found on the connected micro:bit.
        """
        self.serial = microfs.get_serial(ids=(self.vid,self.pid))
        self.ls()

    def ls(self):
        """
        List the files on the micro:bit. Emit the resulting tuple of filenames
        or emit a failure signal.
        """
        print("esp8266.FileManager.ls")
        try:
            result = tuple(microfs.ls(serial = self.serial))
            self.on_list_files.emit(result)
        except Exception as ex:
            import traceback
            traceback.print_exc(ex)
            logger.exception(ex)
            self.on_list_fail.emit()

    def get(self, microbit_filename, local_filename):
        """
        Get the referenced micro:bit filename and save it to the local
        filename. Emit the name of the filename when complete or emit a
        failure signal.
        """
        try:
            microfs.get(microbit_filename, local_filename)
            self.on_get_file.emit(microbit_filename)
        except Exception as ex:
            logger.error(ex)
            self.on_get_fail.emit(microbit_filename)

    def put(self, local_filename):
        """
        Put the referenced local file onto the filesystem on the micro:bit.
        Emit the name of the file on the micro:bit when complete, or emit
        a failure signal.
        """
        try:
            microfs.put(local_filename, target=None)
            self.on_put_file.emit(os.path.basename(local_filename))
        except Exception as ex:
            logger.error(ex)
            self.on_put_fail.emit(local_filename)

    def delete(self, microbit_filename):
        """
        Delete the referenced file on the micro:bit's filesystem. Emit the name
        of the file when complete, or emit a failure signal.
        """
        try:
            microfs.rm(microbit_filename)
            self.on_delete_file.emit(microbit_filename)
        except Exception as ex:
            logger.error(ex)
            self.on_delete_fail.emit(microbit_filename)

class ESP8266Mode(MicroPythonMode):
    name = _('ESP8266')
    description = _("Write MicroPython for the ESP8266.")
    icon = 'adafruit'
    fs = None  #: Reference to filesystem navigator.
    vid = 0x10C4
    pid = 0xEA60
    valid_boards = [
        (vid, pid),  # Cygnal Integrated Products, Inc. CP210x UART Bridge
    ]
    
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
                'description': _('Access the file system on the esp8266.'),
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
        # Check for micro:bit
        if not microfs.find_microbit(ids=(self.vid, self.pid)):
            message = _('Could not find an attached BBC micro:bit.')
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
        self.file_manager.vid = self.vid
        self.file_manager.pid = self.pid
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
