# ***********************************************************************
# *                                                                     *
# * Copyright (c) 2023 Ondsel                                           *
# *                                                                     *
# ***********************************************************************

import Utils

from PySide import QtCore, QtGui
import os
from datetime import datetime, timedelta
import requests
import json
import shutil
import uuid
import math
import tempfile

import jwt
import FreeCAD
import FreeCADGui as Gui

from DataModels import WorkspaceListModel
from VersionModel import LocalVersionModel, OndselVersionModel
from LinkModel import ShareLinkModel
from APIClient import APIClient
from WorkSpace import WorkSpaceModel, WorkSpaceModelFactory

from PySide.QtGui import (
    QStyledItemDelegate,
    QCheckBox,
    QComboBox,
    QPushButton,
    QLabel,
    QStyle,
    QWidget,
    QMessageBox,
    QHeaderView,
    QApplication,
    QIcon,
    QAction,
    QActionGroup,
    QMenu,
)

mw = Gui.getMainWindow()
p = FreeCAD.ParamGet("User parameter:BaseApp/Ondsel")
modPath = os.path.dirname(__file__).replace("\\", "/")
iconsPath = f"{modPath}/Resources/icons/"
cachePath = f"{modPath}/Cache/"

baseUrl = "http://ec2-54-234-132-150.compute-1.amazonaws.com"
ondselUrl = "https://www.ondsel.com/"


class CheckboxDelegate(QStyledItemDelegate):
    def createEditor(self, parent, option, index):
        checkbox = QCheckBox(parent)
        checkbox.setTristate(False)
        return checkbox

    def setEditorData(self, editor, index):
        value = index.data(QtCore.Qt.DisplayRole)
        checked = (
            value
            if value in (QtCore.Qt.Checked, QtCore.Qt.Unchecked)
            else QtCore.Qt.Unchecked
        )
        editor.setCheckState(checked)

    def setModelData(self, editor, model, index):
        checked = editor.checkState()
        model.setData(index, checked, QtCore.Qt.EditRole)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


# Simple delegate drawing an icon and text
class FileListDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        # Get the data for the current index
        if not index.isValid():
            return

        fileName, status, isFolder = index.data(
            WorkSpaceModel.NameStatusAndIsFolderRole
        )

        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())

        icon_rect = QtCore.QRect(option.rect.left(), option.rect.top(), 16, 16)
        text_rect = QtCore.QRect(
            option.rect.left() + 20,
            option.rect.top(),
            option.rect.width() - 20,
            option.rect.height(),
        )
        if isFolder:
            icon = QtGui.QIcon.fromTheme("back", QtGui.QIcon(":/icons/folder.svg"))
        else:
            icon = QtGui.QIcon.fromTheme(
                "back", QtGui.QIcon(":/icons/document-new.svg")
            )

        icon.paint(painter, icon_rect)
        textToDisplay = fileName
        if status != "":
            textToDisplay += " (" + status + ")"
        painter.drawText(text_rect, QtCore.Qt.AlignLeft, textToDisplay)


class WorkspaceListDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        # Get the data for the current index
        workspaceData = index.data(QtCore.Qt.DisplayRole)

        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())

        # Set up font for the name (bold)
        name_font = painter.font()
        name_font.setBold(True)

        # Set up font for the type (normal)
        type_font = painter.font()
        type_font.setBold(False)

        # Draw the name
        name_rect = QtCore.QRect(
            option.rect.left() + 20,
            option.rect.top(),
            option.rect.width() - 20,
            option.rect.height() // 3,
        )
        painter.setFont(name_font)
        painter.drawText(
            name_rect,
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            workspaceData["name"],
        )

        # Calculate the width of the name text
        name_width = painter.fontMetrics().boundingRect(workspaceData["name"]).width()

        # Draw the type in parentheses
        type_text = f"({workspaceData['type']})"
        type_rect = QtCore.QRect(
            option.rect.left() + 20 + name_width + 5,
            option.rect.top(),
            option.rect.width() - 20,
            option.rect.height() // 3,
        )
        painter.setFont(type_font)
        painter.drawText(
            type_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, type_text
        )

        # Adjust the height of the item
        item_height = option.rect.height() // 3
        name_rect.setHeight(item_height)
        type_rect.setHeight(item_height)

        # Draw the description
        desc_rect = QtCore.QRect(
            option.rect.left() + 20,
            type_rect.bottom(),
            option.rect.width() - 20,
            item_height,
        )
        painter.drawText(
            desc_rect,
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
            workspaceData["description"],
        )

    def sizeHint(self, option, index):
        return QtCore.QSize(100, 60)  # Adjust the desired width and height


class WorkspaceView(QtGui.QDockWidget):
    currentWorkspace = None
    username = "none"
    access_token = ""
    apiClient = None
    user = None

    def __init__(self):
        super(WorkspaceView, self).__init__(mw)
        self.setObjectName("workspaceView")
        self.form = Gui.PySideUic.loadUi(f"{modPath}/WorkspaceView.ui")
        self.setWidget(self.form)
        self.setWindowTitle("Workspace View")

        self.createOndselButtonMenus()

        self.ondselIcon = QIcon(iconsPath + "OndselWorkbench.svg")
        self.ondselIconOff = QIcon(iconsPath + "OndselWorkbench-off.svg")
        #self.form.userBtn.setFixedSize(48,48);
        self.form.userBtn.setIconSize(QtCore.QSize(32, 32));
        self.form.userBtn.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon);
        self.form.userBtn.clicked.connect(self.form.userBtn.showMenu)

        self.form.buttonBack.clicked.connect(self.backClicked)

        self.workspacesModel = WorkspaceListModel()
        self.workspacesDelegate = WorkspaceListDelegate(self)
        self.form.workspaceListView.setModel(self.workspacesModel)
        self.form.workspaceListView.setItemDelegate(self.workspacesDelegate)
        self.form.workspaceListView.doubleClicked.connect(self.enterWorkspace)
        self.form.workspaceListView.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.form.workspaceListView.customContextMenuRequested.connect(
            self.showWorkspaceContextMenu
        )

        self.filesDelegate = FileListDelegate(self)
        self.form.fileList.setItemDelegate(self.filesDelegate)
        self.form.fileList.doubleClicked.connect(self.fileListDoubleClicked)
        self.form.fileList.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.form.fileList.customContextMenuRequested.connect(self.showFileContextMenu)
        self.form.fileList.clicked.connect(self.fileListClicked)

        self.form.versionsView.doubleClicked.connect(self.versionClicked)

        self.form.linksView.doubleClicked.connect(self.linksListDoubleClicked)
        self.form.linksView.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.form.linksView.customContextMenuRequested.connect(
            self.showLinksContextMenu
        )

        addFileMenu = QtGui.QMenu(self.form.addFileBtn)
        addFileAction = QtGui.QAction("Add current file", self.form.addFileBtn)
        addFileAction.triggered.connect(self.addCurrentFile)
        addFileMenu.addAction(addFileAction)
        addFileAction2 = QtGui.QAction("Select files...", self.form.addFileBtn)
        addFileAction2.triggered.connect(self.addFileBtnClicked)
        addFileMenu.addAction(addFileAction2)
        self.form.addFileBtn.setMenu(addFileMenu)

        self.form.WorkspaceDetails.setVisible(True)
        self.form.fileDetails.setVisible(False)

        # Check if user is already logged in.
        loginDataStr = p.GetString("loginData", "")
        if loginDataStr != "":
            loginData = json.loads(loginDataStr)
            self.access_token = loginData["accessToken"]
            # self.access_token = self.generate_expired_token()

            if self.tokenExpired(self.access_token):
                user = None
                self.logout()
            else:
                user = loginData["user"]

            self.setUIForLogin(True, user)
        else:
            user = None
            self.setUIForLogin(False)

        self.switchView()

        checkboxDelegate = CheckboxDelegate()
        linksView = self.form.linksView  # window.findChild(QTableView, "linksView")
        linksView.setItemDelegateForColumn(3, checkboxDelegate)
        # linksView.setModel(self.linksModel)

    # def generate_expired_token(self):
    #     # generate an expired token for testing
    #     expiration_time = datetime.now() - timedelta(minutes=5)  # Set expiration time to 5 minutes ago
    #     payload = {
    #         "exp": expiration_time.timestamp(),
    #         # Add other claims as needed
    #     }
    #     secret_key = "your_secret_key"  # Replace with your secret key
    #     token = jwt.encode(payload, secret_key, algorithm="HS256")
    #     return token

    def createOndselButtonMenus(self):
        # Ondsel Button's menu when logged in
        self.userMenu = QMenu(self.form.userBtn)
        userActions = QActionGroup(self.userMenu)

        a = QAction("Ondsel Account", userActions)
        a.triggered.connect(self.ondselAccount)
        self.userMenu.addAction(a)

        self.synchronizeAction = QAction("Synchronize", userActions)
        self.synchronizeAction.setVisible(False)
        self.userMenu.addAction(self.synchronizeAction)

        a2 = QAction("Add new workspace", userActions)
        a2.triggered.connect(self.newWorkspaceBtnClicked)
        self.userMenu.addAction(a2)

        a3 = QAction("Preferences", userActions)
        a3.triggered.connect(self.openPreferences)
        self.userMenu.addAction(a3)

        a4 = QAction("Log out", userActions)
        a4.triggered.connect(self.logout)
        self.userMenu.addAction(a4)

        # Ondsel Button's menu when user not logged in
        self.guestMenu = QMenu(self.form.userBtn)
        guestActions = QActionGroup(self.guestMenu)

        a5 = QAction("Login", guestActions)
        a5.triggered.connect(self.loginBtnClicked)
        self.guestMenu.addAction(a5)

        a6 = QAction("Sign up", guestActions)
        a6.triggered.connect(self.showOndselSignUpPage)
        self.guestMenu.addAction(a6)

        self.guestMenu.addAction(a2)

    def tokenExpired(self, token):
        try:
            decoded_token = jwt.decode(
                token,
                audience="https://yourdomain.com",
                options={"verify_signature": False},
            )
        except Exception as e:
            print(e)
            print(token)
        expiration_time = datetime.fromtimestamp(decoded_token["exp"])
        current_time = datetime.now()
        return current_time > expiration_time

    def setUIForLogin(self, state, user=None):
        """Toggle the visibility of UI elements based on if user is logged in"""
        
        if state:
            self.form.userBtn.setText(
                user["lastName"] + " " + user["firstName"][:1] + "."
            )
            self.form.userBtn.setIcon(self.ondselIcon)
            self.form.userBtn.setMenu(self.userMenu)
        else:
            self.form.userBtn.setText("Local Only")
            self.form.userBtn.setIcon(self.ondselIconOff)
            self.form.userBtn.setMenu(self.guestMenu)


    def enterWorkspace(self, index):
        print("entering workspace")

        self.currentWorkspace = self.workspacesModel.data(index)

        # Create a workspace model and set it to the list
        if self.currentWorkspace["type"] == "Ondsel":
            if self.apiClient is None and self.access_token is None:
                print("You need to login first")
                self.loginBtnClicked()
                self.enterWorkspace(index)
                return

            if self.apiClient is None and self.access_token is not None:
                self.apiClient = APIClient(
                    baseUrl, "", "", self.access_token, self.user
                )

        self.currentWorkspaceModel = WorkSpaceModelFactory.createWorkspace(
            self.currentWorkspace, API_Client=self.apiClient
        )

        self.form.workspaceNameLabel.setText(
            self.currentWorkspaceModel.getWorkspacePath()
        )

        self.form.fileList.setModel(self.currentWorkspaceModel)
        self.synchronizeAction.setVisible(True)
        self.synchronizeAction.triggered.connect(self.currentWorkspaceModel.refreshModel)

        self.switchView()

    def leaveWorkspace(self):
        if self.currentWorkspace is None:
            return
        self.synchronizeAction.setVisible(False)
        self.synchronizeAction.triggered.disconnect()
        self.currentWorkspace = None
        self.currentWorkspaceModel = None
        self.form.fileList.setModel(None)
        self.switchView()
        self.form.workspaceNameLabel.setText("")
        self.form.fileDetails.setVisible(False)

    def switchView(self):
        print("switchView")
        isFileView = self.currentWorkspace is not None
        self.form.workspaceListView.setVisible(not isFileView)
        self.form.buttonBack.setVisible(isFileView)
        self.form.addFileBtn.setVisible(isFileView)
        self.form.fileList.setVisible(isFileView)
        self.form.workspaceNameLabel.setVisible(isFileView)

    def backClicked(self):
        if self.currentWorkspace is None:
            return

        subPath = self.currentWorkspaceModel.subPath

        if subPath == "":
            self.leaveWorkspace()
        else:
            self.currentWorkspaceModel.openParentFolder()
            self.form.workspaceNameLabel.setText(
                self.currentWorkspaceModel.getWorkspacePath()
            )

    def fileListDoubleClicked(self, index):
        print("fileListDoubleClicked")

        self.currentWorkspaceModel.openFile(index)

        self.form.workspaceNameLabel.setText(
            self.currentWorkspaceModel.getWorkspacePath()
        )

    def linksListDoubleClicked(self, index):
        print("linksListDoubleClicked")

        self.currentWorkspaceModel.openLink(index)

        self.form.workspaceNameLabel.setText(
            self.currentWorkspaceModel.getWorkspacePath()
        )

    def versionClicked(self, index):
        model = self.form.versionsView.model()
        backupfilename = model.data(index, role=QtCore.Qt.UserRole)

        idx = self.form.fileList.currentIndex()
        fileName = self.currentWorkspaceModel.data(idx, WorkSpaceModel.NameRole)
        fullFileName = f"{self.currentWorkspace['url']}/{fileName}"

        # Create the QMessageBox dialog
        message_box = QMessageBox()
        message_box.setWindowTitle("Confirmation")
        message_box.setText(
            "You are reverting to a backup file.\nDo you want to save the current version as new backup or discard the changes?",
        )
        message_box.setStandardButtons(
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel
        )

        # Show the dialog and retrieve the user's choice
        choice = message_box.exec_()

        # Process the user's choice
        if choice == QMessageBox.Save:

            # make sure the document is open and get a reference
            doc = FreeCAD.open(fullFileName)

            # create a temporary copy of the backup so it isn't lost by backup policy
            temp_dir = tempfile.gettempdir()
            temp_file = tempfile.NamedTemporaryFile(dir=temp_dir, delete=False)
            temp_file_path = temp_file.name
            temp_file.close()

            shutil.copy(backupfilename, temp_file_path)

            # Save and close the original to create the new backup
            doc.save()
            FreeCAD.closeDocument(doc.Name)

            # move the tempfile back to the original
            try:
                shutil.move(temp_file_path, fullFileName)
            except OSError as e:
                print(f"Error renaming file: {e}")

            # reopen the original
            doc = FreeCAD.open(fullFileName)

        elif choice == QMessageBox.Discard:

            try:
                shutil.move(backupfilename, fullFileName)
            except OSError as e:
                print(f"Error renaming file: {e}")

            doc = FreeCAD.open(fullFileName)
            doc.restore()

        elif choice == QMessageBox.Cancel:
            return

    def fileListClicked(self, index):

        version_tab = self.form.fileDetails.widget(0)
        link_tab = self.form.fileDetails.widget(1)

        fileName, isFolder = self.currentWorkspaceModel.data(
            index, WorkSpaceModel.NameAndIsFolderRole
        )

        if isFolder:
            version_model = None
            links_model = None

        elif self.currentWorkspace["type"] == "Local":
            version_tab.setDisabled(False)
            fullFileName = f"{self.currentWorkspaceModel.getFullPath()}/{fileName}"
            self.form.fileDetails.setVisible(True)

            version_model = LocalVersionModel(fullFileName)
            links_model = None

            link_tab.setEnabled(False)
            version_tab.setEnabled(True)

        elif self.currentWorkspace["type"] == "Ondsel":

            fileId = self.currentWorkspaceModel.data(index, WorkSpaceModel.IdRole)
            print(f"fileId: {fileId}")

            if fileId is not None:
                links_model = ShareLinkModel(fileId, self.apiClient)
                version_model = None

                self.form.linksView.setModel(links_model)
                self.form.versionsView.setModel(None)
                self.form.fileDetails.setVisible(True)

            version_tab.setEnabled(False)
            link_tab.setEnabled(True)
        else:
            self.form.fileDetails.setVisible(False)
            version_tab.setEnabled(False)
            link_tab.setEnabled(False)
            links_model = None
            version_model = None

        self.form.versionsView.setModel(version_model)
        self.form.linksView.setModel(links_model)

        if version_model is not None:
            # Set the size policy of the second column to expand
            self.form.versionsView.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.Stretch
            )

        if links_model is not None:
            self.form.linksView.horizontalHeader().setSectionResizeMode(
                1, QHeaderView.Stretch
            )

        # Adjust the header to fill the entire width
        self.form.versionsView.horizontalHeader().setStretchLastSection(True)
        self.form.linksView.horizontalHeader().setStretchLastSection(True)

    def showWorkspaceContextMenu(self, pos):
        index = self.form.workspaceListView.indexAt(pos)

        menu = QtGui.QMenu()

        deleteAction = menu.addAction("Delete")
        action = menu.exec_(self.form.workspaceListView.viewport().mapToGlobal(pos))

        if action == deleteAction:
            result = QtGui.QMessageBox.question(
                self,
                "Delete Workspace",
                "Are you sure you want to delete this workspace?",
                QtGui.QMessageBox.Yes | QtGui.QMessageBox.No,
            )
            if result == QtGui.QMessageBox.Yes:
                self.workspacesModel.removeWorkspace(index)

    def showFileContextMenu(self, pos):
        index = self.form.fileList.indexAt(pos)
        fileId = self.currentWorkspaceModel.data(index, WorkSpaceModel.IdRole)

        menu = QtGui.QMenu()
        openOnlineAction = menu.addAction("Open Online")
        # shareAction = menu.addAction("Share")
        uploadAction = menu.addAction("Upload to Lens")
        downloadAction = menu.addAction("Download from Lens")
        menu.addSeparator()
        deleteAction = menu.addAction("Delete File")

        action = menu.exec_(self.form.fileList.viewport().mapToGlobal(pos))

        if action == openOnlineAction:
            url = ondselUrl

            if self.currentWorkspace["type"] == "Ondsel" and fileId is not None:
                url = f"{baseUrl}:8080/model/{fileId}"

            QtGui.QDesktopServices.openUrl(QtCore.QUrl(url))

        elif action == deleteAction:
            result = QtGui.QMessageBox.question(
                self.form.fileList,
                "Delete File",
                "Are you sure you want to delete this file?",
                QtGui.QMessageBox.Yes | QtGui.QMessageBox.No,
            )
            if result == QtGui.QMessageBox.Yes:
                self.currentWorkspaceModel.deleteFile(index)
        elif self.currentWorkspace["type"] == "Ondsel":
            if action == downloadAction:
                self.currentWorkspaceModel.downloadFile(index)
            elif action == uploadAction:
                self.currentWorkspaceModel.uploadFile(index)

    def showLinksContextMenu(self, pos):
        index = self.form.linksView.indexAt(pos)
        model = self.form.linksView.model()

        if index.isValid():
            menu = QtGui.QMenu()
            linkId = model.data(index, ShareLinkModel.UrlRole)
            copyLinkAction = menu.addAction("copy link")
            editLinkAction = menu.addAction("edit")
            deleteAction = menu.addAction("Delete")

            action = menu.exec_(self.form.linksView.viewport().mapToGlobal(pos))

            if action == copyLinkAction:
                url = model.compute_url(linkId)
                clipboard = QApplication.clipboard()
                clipboard.setText(url)

            elif action == editLinkAction:
                linkData = model.data(index, ShareLinkModel.EditLinkRole)

                dialog = SharingLinkEditDialog(linkData, self)

                if dialog.exec_() == QtGui.QDialog.Accepted:
                    link_properties = dialog.getLinkProperties()
                    model.update_link(index, link_properties)

            elif action == deleteAction:
                result = QtGui.QMessageBox.question(
                    None,
                    "Delete Link",
                    "Are you sure you want to delete this link?",
                    QtGui.QMessageBox.Yes | QtGui.QMessageBox.No,
                )
                if result == QtGui.QMessageBox.Yes:
                    model.delete_link(linkId)

        else:
            menu = QtGui.QMenu()
            addLinkAction = menu.addAction("add link")

            action = menu.exec_(self.form.linksView.viewport().mapToGlobal(pos))

            if action == addLinkAction:
                dialog = SharingLinkEditDialog(None, self)

                if dialog.exec_() == QtGui.QDialog.Accepted:
                    link_properties = dialog.getLinkProperties()

                    model.add_new_link(link_properties)

    def openPreferences(self):
        print("Preferences clicked")

    def ondselAccount(self):
        url = f"{baseUrl}:8080/login"
        QtGui.QDesktopServices.openUrl(url)

    def showOndselSignUpPage(self):
        url = f"{baseUrl}:8080/signup"
        QtGui.QDesktopServices.openUrl(url)

    def loginBtnClicked(self):

        # Show a login dialog to get the user's email and password
        dialog = LoginDialog()
        if dialog.exec_() == QtGui.QDialog.Accepted:
            email, password = dialog.get_credentials()
            self.apiClient = APIClient(baseUrl, email, password)
            self.apiClient._authenticate()

            # Check if the request was successful (201 status code)
            if self.apiClient.access_token is not None:
                loginData = {
                    "accessToken": self.apiClient.access_token,
                    "user": self.apiClient.user,
                }
                p.SetString("loginData", json.dumps(loginData))

                self.access_token = self.apiClient.access_token

                self.setUIForLogin(True, self.apiClient.user)

                self.workspacesModel.addWorkspace(
                    "Ondsel",
                    "For now single Ondsel workspace of user",
                    "Ondsel",
                    cachePath + "ondsel",
                )
            else:
                print("Authentication failed")

    def logout(self):
        self.setUIForLogin(False)
        p.SetString("loginData", "")
        self.access_token = ""

        self.leaveWorkspace()

        self.workspacesModel.removeOndselWorkspaces()

    def addCurrentFile(self):
        # Save current file on the server.
        doc = FreeCAD.ActiveDocument

        # Get the default name of the file from the document
        default_name = doc.Label + ".FCStd"
        default_path = self.currentWorkspaceModel.getFullPath()
        default_file_path = Utils.joinPath(default_path, default_name)

        # Open a dialog box for the user to select a file location and name
        file_name, _ = QtGui.QFileDialog.getSaveFileName(
            self, "Save File", default_file_path, "FreeCAD file (*.fcstd)"
        )

        if file_name:
            # Make sure the file has the correct extension
            if not file_name.lower().endswith(".fcstd"):
                file_name += ".FCStd"

            # Save the file
            FreeCAD.Console.PrintMessage(f"Saving document to file: {file_name}\n")
            doc.saveAs(file_name)

    def addFileBtnClicked(self):
        # open file browser dialog to select files to copy
        selectedFiles, _ = QtGui.QFileDialog.getOpenFileNames(
            None,
            "Select Files",
            os.path.expanduser("~"),
            "All Files (*);;Text Files (*.txt)",
        )

        # copy selected files to destination folder
        for fileUrl in selectedFiles:
            fileName = os.path.basename(fileUrl)

            destFileUrl = Utils.joinPath(
                self.currentWorkspaceModel.getFullPath(), fileName
            )

            if Utils.isOpenableByFreeCAD(fileName):
                try:
                    shutil.copy(fileUrl, destFileUrl)
                except:
                    QtGui.QMessageBox.warning(
                        None, "Error", "Failed to copy file " + fileName
                    )

    def newWorkspaceBtnClicked(self):
        dialog = NewWorkspaceDialog()
        # Show the dialog and wait for the user to close it
        if dialog.exec_() == QtGui.QDialog.Accepted:
            workspaceName = dialog.nameEdit.text()
            workspaceDesc = dialog.descEdit.toPlainText()
            workspaceType = ""
            workspaceUrl = ""

            # Determine workspace type and get corresponding values
            if dialog.localRadio.isChecked():
                workspaceType = "Local"
                workspaceUrl = dialog.localFolderLabel.text()
            elif dialog.ondselRadio.isChecked():
                workspaceType = "Ondsel"
                workspaceUrl = cachePath + dialog.nameEdit.text()
            else:
                workspaceType = "External"
                workspaceUrl = dialog.externalServerEdit.text()

            # Update workspaceListWidget with new workspace
            self.workspacesModel.addWorkspace(
                workspaceName, workspaceDesc, workspaceType, workspaceUrl
            )


class NewWorkspaceDialog(QtGui.QDialog):
    def __init__(self, parent=None):
        super(NewWorkspaceDialog, self).__init__(parent)
        self.setWindowTitle("Add Workspace")
        self.setModal(True)

        layout = QtGui.QVBoxLayout()

        # Radio buttons for selecting workspace type
        self.localRadio = QtGui.QRadioButton("Local")
        self.ondselRadio = QtGui.QRadioButton("Ondsel Server")
        self.ondselRadio.setToolTip(
            "Ondsel currently supports only one workspace that is added automatically on login."
        )
        self.ondselRadio.setEnabled(False)
        self.externalRadio = QtGui.QRadioButton("External Server")
        self.externalRadio.setToolTip(
            "Currently external servers support is not implemented."
        )
        self.externalRadio.setEnabled(False)

        button_group = QtGui.QButtonGroup()
        button_group.addButton(self.localRadio)
        button_group.addButton(self.ondselRadio)
        button_group.addButton(self.externalRadio)

        group_box = QtGui.QGroupBox("type")
        group_box_layout = QtGui.QHBoxLayout()
        group_box_layout.addWidget(self.localRadio)
        group_box_layout.addWidget(self.ondselRadio)
        group_box_layout.addWidget(self.externalRadio)
        group_box.setLayout(group_box_layout)

        # Workspace Name
        self.nameLabel = QtGui.QLabel("Name")
        self.nameEdit = QtGui.QLineEdit()
        nameHlayout = QtGui.QHBoxLayout()
        nameHlayout.addWidget(self.nameLabel)
        nameHlayout.addWidget(self.nameEdit)

        # Workspace description
        self.descLabel = QtGui.QLabel("Description")
        self.descEdit = QtGui.QTextEdit()

        # Widgets for local workspace type
        self.localFolderLabel = QtGui.QLineEdit("")
        self.localFolderEdit = QtGui.QPushButton("Select folder")
        self.localFolderEdit.clicked.connect(self.show_folder_picker)
        h_layout = QtGui.QHBoxLayout()
        h_layout.addWidget(self.localFolderLabel)
        h_layout.addWidget(self.localFolderEdit)

        # Widgets for external server workspace type
        self.externalServerLabel = QtGui.QLabel("Server URL")
        self.externalServerEdit = QtGui.QLineEdit()

        # Add widgets to layout
        layout.addWidget(group_box)
        layout.addLayout(nameHlayout)
        layout.addWidget(self.descLabel)
        layout.addWidget(self.descEdit)
        layout.addLayout(h_layout)
        layout.addWidget(self.externalServerLabel)
        layout.addWidget(self.externalServerEdit)

        # Connect radio buttons to updateDialog function
        self.localRadio.toggled.connect(self.updateDialog)
        self.ondselRadio.toggled.connect(self.updateDialog)
        self.externalRadio.toggled.connect(self.updateDialog)
        self.localRadio.setChecked(True)

        # Add OK and Cancel buttons
        buttonBox = QtGui.QDialogButtonBox(
            QtGui.QDialogButtonBox.Ok | QtGui.QDialogButtonBox.Cancel
        )
        buttonBox.accepted.connect(self.okClicked)
        buttonBox.rejected.connect(self.reject)

        # Add layout and buttons to dialog
        self.setLayout(layout)
        layout.addWidget(buttonBox)

    # Function to update the dialog when the workspace type is changed
    def updateDialog(self):
        if self.ondselRadio.isChecked():
            self.nameLabel.setText("ondsel.com/")
        else:
            self.nameLabel.setText("Name")

        self.localFolderLabel.setVisible(self.localRadio.isChecked())
        self.localFolderEdit.setVisible(self.localRadio.isChecked())

        self.externalServerLabel.setVisible(self.externalRadio.isChecked())
        self.externalServerEdit.setVisible(self.externalRadio.isChecked())

    def show_folder_picker(self):
        options = QtGui.QFileDialog.Options()
        options |= QtGui.QFileDialog.ShowDirsOnly
        folder_url = QtGui.QFileDialog.getExistingDirectory(
            self, "Select Folder", options=options
        )
        if folder_url:
            self.localFolderLabel.setText(folder_url)

    def okClicked(self):
        if self.localRadio.isChecked():
            if os.path.isdir(self.localFolderLabel.text()):
                self.accept()
            else:
                result = QtGui.QMessageBox.question(
                    self,
                    "Wrong URL",
                    "The URL you entered is not correct.",
                    QtGui.QMessageBox.Ok,
                )


class SharingLinkEditDialog(QtGui.QDialog):
    def __init__(self, linkProperties=None, parent=None):
        super(SharingLinkEditDialog, self).__init__(parent)

        # Load the UI from the .ui file
        self.dialog = Gui.PySideUic.loadUi(modPath + "/SharingLinkEditDialog.ui")

        layout = QtGui.QVBoxLayout()
        layout.addWidget(self.dialog)
        self.setLayout(layout)

        self.dialog.okBtn.clicked.connect(self.accept)
        self.dialog.cancelBtn.clicked.connect(self.reject)

        if linkProperties is None:

            self.linkProperties = {
                "description": "",
                "canViewModelAttributes": True,
                "canUpdateModel": True,
                "canExportFCStd": True,
                "canExportSTEP": True,
                "canExportSTL": True,
                "canExportOBJ": True,
                "isActive": True,
                "canViewModel": True,
                "canDownloadDefaultModel": True,
            }
        else:
            self.linkProperties = linkProperties

        self.setLinkProperties()

    def setLinkProperties(self):
        print(self.linkProperties)
        self.dialog.linkName.setText(self.linkProperties["description"])
        self.dialog.canViewModelAttributesCheckBox.setChecked(
            self.linkProperties["canViewModelAttributes"]
        )
        self.dialog.canUpdateModelCheckBox.setChecked(
            self.linkProperties["canUpdateModel"]
        )
        self.dialog.canExportFCStdCheckBox.setChecked(
            self.linkProperties["canExportFCStd"]
        )
        self.dialog.canExportSTEPCheckBox.setChecked(
            self.linkProperties["canExportSTEP"]
        )
        self.dialog.canExportSTLCheckBox.setChecked(self.linkProperties["canExportSTL"])
        self.dialog.canExportOBJCheckBox.setChecked(self.linkProperties["canExportOBJ"])

    def getLinkProperties(self):

        self.linkProperties["description"] = self.dialog.linkName.text()
        self.linkProperties[
            "canViewModelAttributes"
        ] = self.dialog.canViewModelAttributesCheckBox.isChecked()
        self.linkProperties[
            "canUpdateModel"
        ] = self.dialog.canUpdateModelCheckBox.isChecked()
        self.linkProperties[
            "canExportFCStd"
        ] = self.dialog.canExportFCStdCheckBox.isChecked()
        self.linkProperties[
            "canExportSTEP"
        ] = self.dialog.canExportSTEPCheckBox.isChecked()
        self.linkProperties[
            "canExportSTL"
        ] = self.dialog.canExportSTLCheckBox.isChecked()
        self.linkProperties[
            "canExportOBJ"
        ] = self.dialog.canExportOBJCheckBox.isChecked()

        return self.linkProperties


class LoginDialog(QtGui.QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Login")
        self.email_label = QtGui.QLabel("Email:")
        self.email_input = QtGui.QLineEdit()
        self.password_label = QtGui.QLabel("Password:")
        self.password_input = QtGui.QLineEdit()
        self.password_input.setEchoMode(QtGui.QLineEdit.Password)
        self.submit_button = QtGui.QPushButton("Login")
        self.submit_button.clicked.connect(self.accept)
        layout = QtGui.QVBoxLayout()
        layout.addWidget(self.email_label)
        layout.addWidget(self.email_input)
        layout.addWidget(self.password_label)
        layout.addWidget(self.password_input)
        layout.addWidget(self.submit_button)
        self.setLayout(layout)

    def get_credentials(self):
        email = self.email_input.text()
        password = self.password_input.text()
        return email, password
