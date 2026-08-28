def check_missing(df):
    for col in df.columns:
        if df[col].dtype == 'object':
            null = df[col].isnull()
            if null.sum() > 0:
                print(df.loc[null], "FOUND NULL")

            trailing = df[col] != df[col].str.strip()
            if trailing.sum() > 0:
                print(df.loc[trailing], "FOUND TRAILING")

            blank = df[col].str.strip() == ''
            if blank.sum() > 0:
                print(df.loc[blank], "FOUND BLANK")

    row_duplicate = df.duplicated()
    if row_duplicate.sum() > 0:
        print(df.loc[row_duplicate], "FOUND DUPLICATE ROW")