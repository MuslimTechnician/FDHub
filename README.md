# FDHub Repository

Welcome to our automated F-Droid binary repository! This repository automatically fetches and publishes open-source Android apps directly from their original GitHub releases, ensuring you always get the latest, unmodified APKs directly from the developers.

## 📱 How to Use (Add to F-Droid)

To install these apps on your Android device, you need an F-Droid client (like [F-Droid](https://f-droid.org/), [Neo Store](https://github.com/NeoApplications/Neo-Store), or [Droid-ify](https://github.com/Droid-ify/client)).

Add the following repository URL to your client:

```text
https://muslimtechnician.github.io/FDHub/repo
```

Once added, the apps will appear in your catalog and automatically receive updates!

---

## 🤝 How to Add a New App (Submit a PR)

Want to see your favorite open-source GitHub app in this repository? Anyone can add a new app by submitting a Pull Request!

1. **Fork** this repository.
2. **Create a new JSON file** inside the `apps/` folder. The file name must be the exact Android Package ID of the app (e.g., `org.example.app.json`).
3. **Fill in the app details** using the simple template below.
4. **Submit a Pull Request** to this repository.

Once your PR is merged, the automated system will instantly pick up the app, download its latest release, and publish it to the F-Droid repository for everyone!

### App Configuration Template

Here is a minimal configuration example. Just change the `id`, `name`, and `github` fields:

```json
{
  "id": "com.github.developer.appname",
  "name": "My Favorite Open Source App",
  "github": "developer/repository-name",
  "assets": {
    "include": ["*.apk"],
    "exclude": ["*-debug.apk", "*-unsigned.apk"]
  },
  "metadata": {
    "summary": "A short one-line description of the app",
    "description": "A longer description of the app explaining its features.",
    "categories": ["Productivity"]
  }
}
```

*Note: The system only tracks official GitHub Releases. The app must attach pre-compiled `.apk` files to their GitHub Releases to be supported.*
