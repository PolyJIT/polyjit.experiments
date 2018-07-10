from setuptools import setup, find_packages

with open('README.md') as f:
    long_description = f.read()

setup(
    name='polyjit.experiments',
    use_scm_version=True,
    url='https://github.com/PolyJIT/polyjit.experiments',
    packages=find_packages(),
    setup_requires=["setuptools_scm"],
    tests_require=["pytest"],
    install_requires=["benchbuild==3.3.0"],
    author="Andreas Simbuerger",
    author_email="simbuerg@fim.uni-passau.de",
    description="Additional experiments used by PolyJIT with BenchBuild.",
    long_description=long_description,
    long_description_content_type='text/markdown',
    license="MIT",
    classifiers=[
        'Development Status :: 4 - Beta', 'Intended Audience :: Developers',
        'Topic :: Software Development :: Testing',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3'
    ],
    keywords="benchbuild experiments")
