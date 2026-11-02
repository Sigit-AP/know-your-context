// async/yield methods.
public async string fetch_one(string url) {
    return url;
}

public void run() {
    fetch_one.begin("u", (obj, res) => {
        try {
            fetch_one.end(res);
        } catch (Error e) {
            warning(e.message);
        }
    });
}

public async int count_of(string[] urls) {
    return urls.length;
}
