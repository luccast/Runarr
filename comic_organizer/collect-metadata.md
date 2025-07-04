After matching issues, we can name the folders and files according to this schema:
Folder Format: $Series ($Year)
    - Example: $Series ($Year) = X-Men (2011)

File Format: 
- $Series $VolumeY $Annual #$Issue ($monthname $Year)
    - Example: $Series $VolumeY $Annual #$Issue ($monthname $Year) = X-Men V2021 #022 (July 2023)
    - There many be Annual issues which as seperate from the other issues. If the file contains the word "Annual", we should add Annual to the filename.

ComicInfo.xml file, Book metadata.
After matching the comic book, Create a ComicInfo.xml file in the series folder directory which contains:

- Year, Month, and Day to form the Release Date
- Writer, Penciller, Inker, Colorist, Letterer, CoverArtist, Editor, and Translator as Authors with the according role. A value with multiple names separated by a , will be split in different authors.
- Title, Summary, Number
- Valid Web links as a book link
- The Tags element will be split by , and added to the book's tags
- If the GTIN element contains a valid ISBN, as the book's ISBN