import tkinter as tk

from ui.meeting_app import AIMeetingSummaryApp


def main():

    root = tk.Tk()

    app = AIMeetingSummaryApp(root)

    root.mainloop()


if __name__ == "__main__":
    main()